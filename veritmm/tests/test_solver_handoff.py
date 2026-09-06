"""Solver handoff tests: typed routing information on unsupported physics.

The handoff is informational only — it adds machine-readable routing
(suggested_solver_family, handoff_hints, informational_only) to typed
failures without changing any capability decision, any verdict, or any
certificate acceptance.  Failures without a reliable recommendation keep
their historical serialization shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tmm_engine import (
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
from tmm_engine.preflight import preflight_path


def _task(**physics) -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec("tio2", 60.0),),
            incident=MediumSpec.air(),
            exit=MediumSpec("sio2"),
        ),
        spectrum=SpectralGrid(500.0, 600.0, 11),
        illumination=IlluminationSpec((0.0,), ("s",)),
        physics=PhysicsRequirements(**physics),
    )


EXPECTED_HANDOFFS = {
    "lateral_periodic": "rcwa",
    "arbitrary_2d": "fdtd_or_fem",
    "arbitrary_3d": "fdtd_or_fem",
    "anisotropic": "berreman_4x4",
    "magneto_optic": "magneto_optic_4x4",
    "nonlinear": "nonlinear_time_domain",
    "finite_beam": "beam_propagation",
    "dipole": "dipole_near_field",
    "mode_source": "eigenmode_expansion",
}


@pytest.mark.parametrize(
    ("boundary", "physics"),
    [
        ("lateral_periodic", {"geometry_class": "lateral_periodic"}),
        ("arbitrary_2d", {"geometry_class": "arbitrary_2d"}),
        ("arbitrary_3d", {"geometry_class": "arbitrary_3d"}),
        ("anisotropic", {"material_class": "anisotropic"}),
        ("magneto_optic", {"material_class": "magneto_optic"}),
        ("nonlinear", {"material_class": "nonlinear"}),
        ("finite_beam", {"excitation_class": "finite_beam"}),
        ("dipole", {"excitation_class": "dipole"}),
        ("mode_source", {"excitation_class": "mode_source"}),
    ],
)
def test_each_boundary_carries_informational_routing(
    boundary: str, physics: dict
) -> None:
    failure = assess_failure(physics)
    assert failure["suggested_solver_family"] == EXPECTED_HANDOFFS[boundary]
    hints = failure["handoff_hints"]
    assert hints["required_inputs"], "handoff hints must name required inputs"
    assert hints["notes"]
    assert failure["informational_only"] is True


def assess_failure(physics: dict) -> dict:
    from tmm_engine import assess_tmm_capability

    assessment = assess_tmm_capability(_task(**physics))
    assert assessment.supported is False
    return assessment.failures[0].to_dict()


def test_time_domain_boundary_carries_the_fdtd_family() -> None:
    failure = assess_failure({"time_domain_required": True})
    assert failure["suggested_solver_family"] == "fdtd"
    assert failure["informational_only"] is True


def test_output_combination_failures_keep_their_historical_shape() -> None:
    """No reliable solver recommendation: no family, no hints, no marker."""

    task = SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(None, 100.0, constant_n=1.5, coherence="incoherent"),),
            incident=MediumSpec.air(),
            exit=MediumSpec("sio2"),
        ),
        spectrum=SpectralGrid(500.0, 600.0, 11),
        illumination=IlluminationSpec((0.0,), ("s",)),
        requested_outputs=("ellipsometry",),
    )
    from tmm_engine import assess_tmm_capability

    assessment = assess_tmm_capability(task)
    assert assessment.supported is False
    failure = assessment.failures[0].to_dict()
    assert failure["code"] == "unsupported_output_combination"
    assert "handoff_hints" not in failure
    assert "informational_only" not in failure


def test_certificate_failures_carry_the_handoff(tmp_path: Path) -> None:
    """The rejected-run certificate routes to the 4x4 family, informationally."""

    task = _task(material_class="anisotropic")
    certified = certify_simulation(TMMWorkbench(MaterialRegistry()), task)
    assert certified.certificate["accepted"] is False
    failure = certified.certificate["failures"][0]
    assert failure["suggested_solver_family"] == "berreman_4x4"
    assert failure["informational_only"] is True
    assert failure["handoff_hints"]["required_inputs"]


def test_preflight_failures_pass_the_handoff_through(tmp_path: Path) -> None:
    task_file = tmp_path / "task.json"
    task_file.write_text(
        json.dumps(
            {
                "mode": "simulate",
                "simulation": {
                    "stack": {
                        "layers": [{"material": "tio2", "thickness_nm": 60.0}],
                        "incident": {"constant_n": 1.0},
                        "exit": {"material": "sio2"},
                    },
                    "spectrum": {"start_nm": 500.0, "stop_nm": 600.0, "points": 11},
                    "illumination": {"angles_deg": [0.0], "polarizations": ["s"]},
                    "physics": {"material_class": "anisotropic"},
                },
            }
        )
    )
    payload = preflight_path(task_file)
    capability_failures = [
        item
        for item in payload.get("failures", [])
        if item["code"] == "unsupported_material_model"
    ]
    assert capability_failures, payload.get("failures")
    assert capability_failures[0]["suggested_solver_family"] == "berreman_4x4"
    assert capability_failures[0]["informational_only"] is True
