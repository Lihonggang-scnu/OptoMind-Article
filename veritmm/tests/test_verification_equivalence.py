"""Byte-level equivalence gate for the VerificationEvidence extraction.

The golden certificates under ``tests/fixtures/verification_equivalence/`` were
generated with the pre-refactor monolithic ``certify_simulation()`` and are
committed.  After splitting evidence collection from acceptance judgement, the
certificate for the same task and settings must be exactly equal: same
``accepted``/``status``/``failures``, same convergence, cross-solver report,
tightest margin, referee report, material identity, and — because the
certificate identity hashes its own content — the same ``certificate_id``.

Regenerate the fixtures only when an intentional, reviewed behaviour change
lands: ``py -3.11 tests/test_verification_equivalence.py``

The certificates embed ``runtime`` (Python/platform/NumPy of the generating
machine), so fixtures are compared on the same environment they were produced
on; a cross-platform equivalence statement is a separate claim (identity
reproducible / numerically equivalent, see the 1.1 plan).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

import pytest

from tmm_engine import (
    AcceptanceSettings,
    IlluminationSpec,
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    PhysicsRequirements,
    SimulationTask,
    SpectralGrid,
    StackSpec,
    TMMWorkbench,
    certify_simulation,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "verification_equivalence"


@dataclass(frozen=True)
class _Case:
    name: str
    task: SimulationTask
    settings: AcceptanceSettings


def build_cases() -> List[_Case]:
    dbr = StackSpec(
        layers=(LayerSpec("tio2", 90.0), LayerSpec("sio2", 140.0)) * 3,
        incident=MediumSpec.air(),
        exit=MediumSpec("sio2"),
    )
    film = StackSpec(
        layers=(LayerSpec(None, 100.0, constant_n=2.1),),
        incident=MediumSpec.air(),
        exit=MediumSpec(constant_n=1.5),
    )
    absorber = StackSpec(
        layers=(LayerSpec("tio2", 30.0), LayerSpec(None, 60.0, constant_n=1.8, constant_k=0.4)),
        incident=MediumSpec.air(),
        exit=MediumSpec("sio2"),
    )
    mixed_coherence = StackSpec(
        layers=(
            LayerSpec("tio2", 60.0),
            LayerSpec(None, 500.0, constant_n=1.5, coherence="incoherent"),
        ),
        incident=MediumSpec.air(),
        exit=MediumSpec("sio2"),
    )
    return [
        _Case(
            "dbr_accepted_default",
            SimulationTask(
                stack=dbr,
                spectrum=SpectralGrid(400.0, 800.0, 41),
                illumination=IlluminationSpec((0.0, 30.0), ("s", "p")),
            ),
            AcceptanceSettings(),
        ),
        _Case(
            "single_film_byrnes_cross_checked",
            SimulationTask(
                stack=film,
                spectrum=SpectralGrid(450.0, 750.0, 31),
                illumination=IlluminationSpec((0.0, 45.0), ("s", "p")),
                solver="byrnes",
            ),
            AcceptanceSettings(),
        ),
        _Case(
            "convergence_not_requested",
            SimulationTask(
                stack=film,
                spectrum=SpectralGrid(450.0, 750.0, 31),
                illumination=IlluminationSpec((0.0,), ("s",)),
            ),
            AcceptanceSettings(require_spectral_convergence=False),
        ),
        _Case(
            "cross_solver_not_requested",
            SimulationTask(
                stack=dbr,
                spectrum=SpectralGrid(400.0, 800.0, 25),
                illumination=IlluminationSpec((0.0,), ("s",)),
            ),
            AcceptanceSettings(require_independent_solver=False),
        ),
        _Case(
            "mixed_coherence_accepted_with_limits",
            SimulationTask(
                stack=mixed_coherence,
                spectrum=SpectralGrid(500.0, 700.0, 21),
                illumination=IlluminationSpec((0.0,), ("s", "p")),
                solver="byrnes",
            ),
            AcceptanceSettings(),
        ),
        _Case(
            "absorbing_stack_energy_failure",
            SimulationTask(
                stack=absorber,
                spectrum=SpectralGrid(500.0, 700.0, 21),
                illumination=IlluminationSpec((0.0,), ("s", "p")),
            ),
            AcceptanceSettings(energy_tolerance=0.0),
        ),
        _Case(
            "unsupported_anisotropic_rejection",
            SimulationTask(
                stack=film,
                spectrum=SpectralGrid(450.0, 750.0, 31),
                illumination=IlluminationSpec((0.0,), ("s",)),
                physics=PhysicsRequirements(material_class="anisotropic"),
            ),
            AcceptanceSettings(),
        ),
        _Case(
            "unknown_material_exception",
            SimulationTask(
                stack=StackSpec(
                    layers=(LayerSpec("not_a_real_material", 50.0),),
                    incident=MediumSpec.air(),
                    exit=MediumSpec(constant_n=1.5),
                ),
                spectrum=SpectralGrid(500.0, 600.0, 11),
                illumination=IlluminationSpec((0.0,), ("s",)),
            ),
            AcceptanceSettings(),
        ),
    ]


def _certificate_for(case: _Case):
    return certify_simulation(
        TMMWorkbench(MaterialRegistry()), case.task, case.settings
    ).certificate


@pytest.mark.parametrize("case", build_cases(), ids=lambda case: case.name)
def test_certificate_matches_pre_refactor_baseline(case: _Case) -> None:
    fixture_path = FIXTURE_DIR / f"{case.name}.json"
    assert fixture_path.is_file(), f"missing golden fixture: {fixture_path}"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    certificate = _certificate_for(case)
    assert certificate == fixture["certificate"], (
        f"certificate for {case.name} diverged from the pre-refactor baseline"
    )


@pytest.mark.parametrize("case", build_cases(), ids=lambda case: case.name)
def test_certificate_identity_is_reproduced(case: _Case) -> None:
    fixture = json.loads(
        (FIXTURE_DIR / f"{case.name}.json").read_text(encoding="utf-8")
    )
    certificate = _certificate_for(case)
    assert certificate["certificate_id"] == fixture["certificate"]["certificate_id"]


def _settings_payload(settings: AcceptanceSettings) -> dict:
    return {
        "require_spectral_convergence": settings.require_spectral_convergence,
        "require_independent_solver": settings.require_independent_solver,
        "cross_solver_tolerance": settings.cross_solver_tolerance,
        "energy_tolerance": settings.energy_tolerance,
    }


def _generate_fixtures() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for case in build_cases():
        certificate = _certificate_for(case)
        payload = {
            "case": case.name,
            "settings": _settings_payload(case.settings),
            "certificate": certificate,
        }
        target = FIXTURE_DIR / f"{case.name}.json"
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {target}")


if __name__ == "__main__":
    _generate_fixtures()
    sys.exit(0)
