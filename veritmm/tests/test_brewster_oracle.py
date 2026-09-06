"""V-13 generalization drill (a): a new analytic oracle case — Brewster's
angle for p polarization — added purely as a test file, with zero core-code
changes.  At the Brewster angle ``tan(theta_B) = n_t / n_i`` the p-polarized
reflectance of a bare interface vanishes; the engine and the analytic oracle
must agree there (and on the neighbouring angles).
"""

from __future__ import annotations

import math

import pytest

from tmm_engine import (
    IlluminationSpec,
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    SimulationTask,
    SpectralGrid,
    StackSpec,
    TMMWorkbench,
)
from tmm_engine.analytic_oracle import interface_power_rt


def _film_on_same_substrate() -> SimulationTask:
    """A film index-matched to the exit medium: optically a bare interface."""

    return SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(None, 1000.0, constant_n=1.5),),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(values_nm=(600.0,)),
        illumination=IlluminationSpec((55.0, 56.309932474050215, 58.0), ("p",)),
        solver="smatrix",
        requested_outputs=("R", "T", "A"),
    )


def test_brewster_angle_zeroes_p_reflectance() -> None:
    workbench = TMMWorkbench(MaterialRegistry())
    result = workbench.simulate(_film_on_same_substrate())

    brewster_deg = math.degrees(math.atan(1.5 / 1.0))
    channel = result.channel(brewster_deg, "p")
    reflectance = float(channel["R"][0])
    oracle_r, oracle_t = interface_power_rt(1.0, 1.5, brewster_deg, "p")

    assert oracle_r == pytest.approx(0.0, abs=1e-18)
    assert reflectance < 1e-12
    assert reflectance == pytest.approx(oracle_r, abs=1e-15)
    assert float(channel["T"][0]) == pytest.approx(oracle_t, rel=1e-12)


def test_engine_matches_oracle_around_brewster() -> None:
    workbench = TMMWorkbench(MaterialRegistry())
    result = workbench.simulate(_film_on_same_substrate())

    for angle_deg in (55.0, 58.0):
        channel = result.channel(angle_deg, "p")
        oracle_r, oracle_t = interface_power_rt(1.0, 1.5, angle_deg, "p")
        assert float(channel["R"][0]) == pytest.approx(oracle_r, rel=1e-12)
        assert float(channel["T"][0]) == pytest.approx(oracle_t, rel=1e-12)
        # direction sanity: reflectance grows on either side of Brewster
    below = float(result.channel(55.0, "p")["R"][0])
    at = float(result.channel(56.309932474050215, "p")["R"][0])
    above = float(result.channel(58.0, "p")["R"][0])
    assert at < below and at < above
