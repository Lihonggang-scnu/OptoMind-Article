"""Deterministic semantic-delivery audit for TMM benchmark runs.

Physics validity answers whether a calculation is trustworthy.  This module
answers the different question of whether the requested analysis was actually
materialized.  Performance values remain soft and are never used as gates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .benchmarks import BenchmarkTask
from .design_task import EngineMode, OpticalDesignTask


class DeliveryCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement: str
    passed: bool
    evidence: tuple[str, ...] = ()
    reason: str


class BenchmarkDeliveryAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "tmm-benchmark-delivery-audit.v1"
    benchmark_id: str
    performance_targets_used_as_gates: bool = False
    checks: tuple[DeliveryCheck, ...]
    passed: bool


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _channel_inventory(simulation: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    angles: set[str] = set()
    polarizations: set[str] = set()
    observables: set[str] = set()
    for channel, payload in (simulation.get("channels") or {}).items():
        for part in str(channel).split("|"):
            if part.startswith("angle="):
                angles.add(part.split("=", 1)[1])
            elif part.startswith("pol="):
                polarizations.add(part.split("=", 1)[1])
        if isinstance(payload, dict):
            observables.update(str(key) for key in payload)
    return angles, polarizations, observables


def _check(requirement: str, passed: bool, evidence: list[str], reason: str) -> DeliveryCheck:
    return DeliveryCheck(
        requirement=requirement,
        passed=bool(passed),
        evidence=tuple(evidence),
        reason=reason,
    )


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def audit_benchmark_delivery(
    benchmark: BenchmarkTask,
    task: OpticalDesignTask | dict[str, Any],
    run_dir: str | Path,
) -> BenchmarkDeliveryAudit:
    root = Path(run_dir).resolve()
    checks: list[DeliveryCheck] = []
    all_angles: set[str] = set()
    all_polarizations: set[str] = set()
    all_observables: set[str] = set()
    objective_rows: dict[str, dict[str, Any]] = {}
    analysis_reports: list[dict[str, Any]] = []
    certificates: list[dict[str, Any]] = []
    portfolios: list[dict[str, Any]] = []
    simulation_paths: list[str] = []
    for experiment in _field(task, "experiments", ()):
        experiment_id = str(_field(experiment, "experiment_id") or "")
        experiment_root = root / "experiments" / experiment_id
        simulation_path = experiment_root / "baseline" / "SIMULATION_RESULT.json"
        if simulation_path.exists():
            simulation = _read(simulation_path)
            angles, polarizations, observables = _channel_inventory(simulation)
            all_angles.update(angles)
            all_polarizations.update(polarizations)
            all_observables.update(observables)
            simulation_paths.append(simulation_path.relative_to(root).as_posix())
        analysis_path = experiment_root / "baseline" / "ANALYSIS_REPORT.json"
        if analysis_path.exists():
            analysis_reports.append(_read(analysis_path))
        certificate_path = experiment_root / "baseline" / "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
        if certificate_path.exists():
            certificates.append(_read(certificate_path))
        portfolio_path = experiment_root / "DESIGN_PORTFOLIO.json"
        if portfolio_path.exists():
            portfolios.append(_read(portfolio_path))
        objective_path = experiment_root / "baseline" / "OBJECTIVE_REPORT.json"
        experiment_objective_rows: dict[str, dict[str, Any]] = {}
        if objective_path.exists():
            experiment_objective_rows = dict(
                _read(objective_path).get("target_attainment") or {}
            )
            objective_rows.update(experiment_objective_rows)
        objectives = tuple(_field(experiment, "objectives", ()) or ())
        mode = str(_field(experiment, "mode") or "")
        if mode in {EngineMode.simulate.value, str(EngineMode.simulate)} and objectives:
            expected_ids = {
                str(_field(item, "objective_id") or "")
                for item in objectives
            }
            missing = sorted(expected_ids - set(experiment_objective_rows))
            checks.append(
                _check(
                    f"declared_forward_objectives:{experiment_id}",
                    not missing,
                    [objective_path.relative_to(root).as_posix()] if objective_path.exists() else [],
                    "all declared objectives were materialized"
                    if not missing
                    else f"missing objective results: {', '.join(missing)}",
                )
            )

    axes = set(benchmark.capability_axes)
    if "angle_sweep" in axes:
        checks.append(
            _check(
                "angle_sweep",
                len(all_angles) >= 2,
                simulation_paths,
                f"observed {len(all_angles)} distinct incidence angles",
            )
        )
    if "polarization_sweep" in axes:
        checks.append(
            _check(
                "polarization_sweep",
                {"s", "p"}.issubset(all_polarizations),
                simulation_paths,
                f"observed polarizations: {sorted(all_polarizations)}",
            )
        )
    required_observables = {
        axis
        for axis, observable in {
            "reflection": "R",
            "transmission": "T",
            "absorption": "A",
            "thermal_emissivity": "A",
            "opaque_stack_emissivity": "A",
        }.items()
        if axis in axes and observable not in all_observables
    }
    checks.append(
        _check(
            "requested_core_observables",
            not required_observables,
            simulation_paths,
            "requested R/T/A-family observables are present"
            if not required_observables
            else f"missing observable capabilities: {', '.join(sorted(required_observables))}",
        )
    )
    if "band_preference" in axes:
        preference_rows = [
            row
            for row in objective_rows.values()
            if row.get("metric")
            in {
                "mean_reflectance",
                "band_reflectance",
                "mean_transmittance",
                "mean_emissivity",
                "mean_absorption",
                "band_emissivity_contrast",
            }
        ]
        has_contrast = any(
            row.get("metric") == "band_emissivity_contrast"
            and "preferred_wavelength_nm" in (row.get("region") or {})
            and "suppressed_wavelength_nm" in (row.get("region") or {})
            for row in preference_rows
        )
        senses = {str(row.get("sense") or "") for row in preference_rows}
        bands = {
            tuple(row.get("region", {}).get("wavelength_nm") or ())
            for row in preference_rows
            if row.get("region", {}).get("wavelength_nm")
        }
        delivered = has_contrast or (senses >= {"maximize", "minimize"} and len(bands) >= 2)
        checks.append(
            _check(
                "band_preference_report",
                delivered,
                [
                    path.relative_to(root).as_posix()
                    for path in root.glob("experiments/*/baseline/OBJECTIVE_REPORT.json")
                ],
                "preferred and suppressed bands were both calculated"
                if delivered
                else "no executable two-band preference report was materialized",
            )
        )
    if any("physics_acceptance" in name for name in benchmark.expected_artifacts):
        checks.append(
            _check(
                "physics_acceptance_certificate",
                bool(certificates) and all(bool(item.get("accepted")) for item in certificates),
                [
                    path.relative_to(root).as_posix()
                    for path in root.glob("experiments/*/baseline/PHYSICS_ACCEPTANCE_CERTIFICATE.json")
                ],
                "deterministic physics certificates are accepted",
            )
        )
    if any("portfolio" in name for name in benchmark.expected_artifacts):
        checks.append(
            _check(
                "design_portfolio",
                bool(portfolios) and any(item.get("candidates") for item in portfolios),
                [
                    path.relative_to(root).as_posix()
                    for path in root.glob("experiments/*/DESIGN_PORTFOLIO.json")
                ],
                "at least one verified portfolio candidate is present",
            )
        )
    if any("rta_report" in name for name in benchmark.expected_artifacts):
        checks.append(
            _check(
                "rta_report",
                {"R", "T", "A"}.issubset(all_observables),
                simulation_paths,
                f"observed channels: {sorted(all_observables)}",
            )
        )
    if any("resonance" in name for name in benchmark.expected_artifacts):
        checks.append(
            _check(
                "resonance_analysis",
                any(report.get("spectral_features") for report in analysis_reports),
                [
                    path.relative_to(root).as_posix()
                    for path in root.glob("experiments/*/baseline/ANALYSIS_REPORT.json")
                ],
                "deterministic spectral features were extracted",
            )
        )
    passed = bool(checks) and all(item.passed for item in checks)
    return BenchmarkDeliveryAudit(
        benchmark_id=benchmark.id,
        checks=tuple(checks),
        passed=passed,
    )


__all__ = [
    "BenchmarkDeliveryAudit",
    "DeliveryCheck",
    "audit_benchmark_delivery",
]
