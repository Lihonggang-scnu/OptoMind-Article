"""Lorentz reciprocity: an opt-in verification dimension with CI anchors.

The check compares forward power transmittance with the reversed stack under
conservation of the in-plane wave vector.  It is off by default (it roughly
doubles the simulations of the acceptance pass) and CI anchors run it
unconditionally.  Stacks in these tests use constant indices so the outer
media are exactly real: absorbing outer media make the mirrored angle
ill-defined and the check reports ``unavailable`` for them honestly.
"""

from __future__ import annotations

import numpy as np
import pytest

import tmm_engine.reciprocity as reciprocity_module
from tmm_engine import (
    AcceptanceSettings,
    IlluminationSpec,
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    SimulationTask,
    SpectralGrid,
    StackSpec,
    TMMWorkbench,
    certify_simulation,
)
from tmm_engine.acceptance import (
    collect_verification_evidence,
    evaluate_evidence,
    verification_policy_dict,
    verification_policy_from_dict,
)
from tmm_engine.analytic_oracle import single_film_power_rt
from tmm_engine.capabilities import FailureCode
from tmm_engine.reciprocity import RECIPROCITY_TOLERANCE, check_reciprocity

WAVELENGTHS = np.linspace(450.0, 750.0, 31)


def _lossless_film() -> StackSpec:
    return StackSpec(
        layers=(LayerSpec(None, 100.0, constant_n=2.1),),
        incident=MediumSpec.air(),
        exit=MediumSpec(constant_n=1.5),
    )


def _lossless_dbr() -> StackSpec:
    return StackSpec(
        layers=(LayerSpec(None, 90.0, constant_n=2.4), LayerSpec(None, 140.0, constant_n=1.46)) * 3,
        incident=MediumSpec.air(),
        exit=MediumSpec(constant_n=1.46),
    )


def _absorbing_stack() -> StackSpec:
    return StackSpec(
        layers=(
            LayerSpec(None, 30.0, constant_n=2.4),
            LayerSpec(None, 60.0, constant_n=1.8, constant_k=0.4),
        ),
        incident=MediumSpec.air(),
        exit=MediumSpec(constant_n=1.5),
    )


def test_lossless_film_and_dbr_are_reciprocal() -> None:
    workbench = TMMWorkbench(MaterialRegistry())
    for stack in (_lossless_film(), _lossless_dbr()):
        report = check_reciprocity(
            workbench, stack, WAVELENGTHS, (0.0, 40.0), ("s", "p")
        )
        assert report["status"] == "passed"
        assert report["max_relative_deviation"] < 1e-10
        assert report["reason"] is None


def test_absorbing_layers_stay_reciprocal() -> None:
    report = check_reciprocity(
        TMMWorkbench(MaterialRegistry()),
        _absorbing_stack(),
        WAVELENGTHS,
        (0.0, 25.0),
        ("s", "p"),
    )
    assert report["status"] == "passed"
    assert report["max_relative_deviation"] < 1e-10


@pytest.mark.parametrize("solver", ["smatrix", "byrnes"])
def test_oblique_polarized_and_unpolarized_channels(solver: str) -> None:
    report = check_reciprocity(
        TMMWorkbench(MaterialRegistry()),
        _lossless_dbr(),
        WAVELENGTHS,
        (15.0, 35.0),
        ("s", "p", "unpolarized"),
        solver=solver,
    )
    assert report["status"] == "passed"
    assert report["max_relative_deviation"] < 1e-10


def test_tampered_backward_transmission_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _lossless_film()
    original = reciprocity_module._transmissions

    def tampered(workbench, task):
        transmissions = original(workbench, task)
        if task.stack.incident == stack.exit:
            # The backward direction is the one whose incident medium is the
            # original exit: corrupt exactly that leg of the comparison.
            return {
                key: values * 0.9 for key, values in transmissions.items()
            }
        return transmissions

    monkeypatch.setattr(reciprocity_module, "_transmissions", tampered)
    report = check_reciprocity(
        TMMWorkbench(MaterialRegistry()), stack, WAVELENGTHS, (20.0,), ("s",)
    )
    assert report["status"] == "failed"
    assert report["max_relative_deviation"] > 1e-3
    assert report["reason"] is not None


def test_oracle_anchors_the_mirror_angle() -> None:
    """The engine's forward and backward T agree with each other and with the
    exact Airy closed forms evaluated in each direction."""

    workbench = TMMWorkbench(MaterialRegistry())
    stack = _lossless_film()
    reversed_stack = StackSpec(
        layers=tuple(reversed(stack.layers)),
        incident=stack.exit,
        exit=stack.incident,
    )
    angle = 30.0
    polarization = "p"
    for wavelength in (475.0, 550.0, 700.0):
        forward_task = SimulationTask(
            stack=stack,
            spectrum=SpectralGrid(values_nm=(wavelength,)),
            illumination=IlluminationSpec((angle,), (polarization,)),
        )
        forward_t = float(
            workbench.simulate(forward_task).channel(angle, polarization)["T"][0]
        )
        mirrored = float(
            np.degrees(
                np.arcsin(np.sin(np.radians(angle)) * 1.0 / 1.5)
            )
        )
        backward_task = SimulationTask(
            stack=reversed_stack,
            spectrum=SpectralGrid(values_nm=(wavelength,)),
            illumination=IlluminationSpec((mirrored,), (polarization,)),
        )
        backward_t = float(
            workbench.simulate(backward_task).channel(mirrored, polarization)["T"][0]
        )
        oracle_forward = single_film_power_rt(
            1.0, complex(2.1), 1.5, 100.0, wavelength, angle, polarization
        )[1]
        oracle_backward = single_film_power_rt(
            1.5, complex(2.1), 1.0, 100.0, wavelength, mirrored, polarization
        )[1]
        assert forward_t == pytest.approx(oracle_forward, abs=1e-12)
        assert backward_t == pytest.approx(oracle_backward, abs=1e-12)
        assert oracle_forward == pytest.approx(oracle_backward, abs=1e-12)


def test_unavailable_cases_are_enumerated_honestly() -> None:
    workbench = TMMWorkbench(MaterialRegistry())

    mixed = StackSpec(
        layers=(
            LayerSpec(None, 40.0, constant_n=1.5),
            LayerSpec(None, 500.0, constant_n=1.5, coherence="incoherent"),
        ),
        incident=MediumSpec.air(),
        exit=MediumSpec(constant_n=1.5),
    )
    report = check_reciprocity(workbench, mixed, WAVELENGTHS, (0.0,), ("s",))
    assert report["status"] == "unavailable"
    assert "mixed-coherence" in report["reason"]
    assert report["max_relative_deviation"] is None

    absorbing_exit = StackSpec(
        layers=(LayerSpec(None, 100.0, constant_n=2.1),),
        incident=MediumSpec.air(),
        exit=MediumSpec(constant_n=1.5, constant_k=0.1),
    )
    report = check_reciprocity(workbench, absorbing_exit, WAVELENGTHS, (0.0,), ("s",))
    assert report["status"] == "unavailable"
    assert "absorbing" in report["reason"]

    # A mirrored angle beyond the exit medium's light cone cannot be launched.
    light_exit = StackSpec(
        layers=(LayerSpec(None, 100.0, constant_n=2.1),),
        incident=MediumSpec.air(),
        exit=MediumSpec(constant_n=0.9),
    )
    report = check_reciprocity(
        workbench, light_exit, WAVELENGTHS, (70.0,), ("s",), max_samples=3
    )
    assert report["status"] == "unavailable"
    assert "light cone" in report["reason"]


def test_acceptance_rejects_on_reciprocity_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _lossless_film()
    original = reciprocity_module._transmissions

    def tampered(workbench, task):
        transmissions = original(workbench, task)
        if task.stack.incident == stack.exit:
            return {key: values * 0.9 for key, values in transmissions.items()}
        return transmissions

    monkeypatch.setattr(reciprocity_module, "_transmissions", tampered)
    settings = AcceptanceSettings(require_reciprocity=True)
    certificate = certify_simulation(
        TMMWorkbench(MaterialRegistry()), _task(stack), settings
    ).certificate

    assert certificate["accepted"] is False
    assert certificate["status"] == "rejected_physics"
    failure = next(
        item
        for item in certificate["failures"]
        if item["code"] == FailureCode.RECIPROCITY_FAILURE.value
    )
    assert failure["recoverable"] is False
    # A reciprocity defect is a numerical/implementation issue: it must not
    # carry solver-handoff routing (no family suggestion, no informational
    # routing marker).
    assert failure["suggested_solver_family"] is None
    assert "handoff_hints" not in failure
    assert "informational_only" not in failure
    assert certificate["reciprocity_check"]["status"] == "failed"


def test_opt_in_check_passes_end_to_end_and_defaults_stay_off() -> None:
    workbench = TMMWorkbench(MaterialRegistry())
    task = _task(_lossless_film())

    on_settings = AcceptanceSettings(require_reciprocity=True)
    evidence, _ = collect_verification_evidence(workbench, task, on_settings)
    assert evidence.reciprocity_check["status"] == "passed"
    certificate = evaluate_evidence(evidence, on_settings)
    assert certificate["accepted"] is True
    assert certificate["reciprocity_check"]["status"] == "passed"

    default_settings = AcceptanceSettings()
    default_evidence, _ = collect_verification_evidence(workbench, task, default_settings)
    assert default_settings.require_reciprocity is False
    assert default_evidence.reciprocity_check is None
    default_certificate = evaluate_evidence(default_evidence, default_settings)
    assert "reciprocity_check" not in default_certificate


def test_reciprocity_flag_persists_through_the_policy() -> None:
    settings = AcceptanceSettings(require_reciprocity=True)
    payload = verification_policy_dict(settings)
    assert payload["require_reciprocity"] is True
    restored = verification_policy_from_dict(payload)
    assert restored.require_reciprocity is True

    # A policy written before the opt-in flag existed loads as False.
    legacy_payload = {
        key: value
        for key, value in verification_policy_dict(AcceptanceSettings()).items()
        if key != "require_reciprocity"
    }
    assert verification_policy_from_dict(legacy_payload).require_reciprocity is False


def test_default_settings_keep_the_certificate_unchanged() -> None:
    certificate = certify_simulation(
        TMMWorkbench(MaterialRegistry()), _task(_lossless_film())
    ).certificate
    assert "reciprocity_check" not in certificate
    assert RECIPROCITY_TOLERANCE == 1e-10


def _task(stack: StackSpec) -> SimulationTask:
    return SimulationTask(
        stack=stack,
        spectrum=SpectralGrid(450.0, 750.0, 31),
        illumination=IlluminationSpec((0.0, 25.0), ("s", "p")),
    )
