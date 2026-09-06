"""Independent absorption accounting: the verifier's second energy ledger.

The declared channels define ``A = 1 - R - T`` and the legacy audit metric
``|R + T + A - 1|`` is therefore an algebraic identity that can never catch a
wrong absorption model on its own.  These tests pin the independent ledger:
``A_independent`` is integrated from layer-resolved dissipation (Byrnes
``absorp_in_each_layer`` / the internal tangential-field Poynting flux) and
must agree with the closure on physical results while exposing injected
absorption errors that the closure is blind to by construction.
"""

from __future__ import annotations

import numpy as np
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
from tmm_engine.analytic_oracle import single_film_power_rt
from tmm_engine.workbench import INDEPENDENCE_AUDIT_FIELDS


def _film_task(solver: str) -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(None, 60.0, constant_n=1.8, constant_k=0.4),),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(450.0, 750.0, 31),
        illumination=IlluminationSpec((0.0, 45.0), ("s", "p")),
        solver=solver,
    )


def _lossless_task(solver: str) -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(None, 100.0, constant_n=2.1),),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(450.0, 750.0, 31),
        illumination=IlluminationSpec((0.0, 45.0), ("s", "p", "unpolarized")),
        solver=solver,
    )


@pytest.mark.parametrize("solver", ["smatrix", "byrnes"])
def test_lossless_film_independent_absorption_vanishes(solver: str) -> None:
    result = TMMWorkbench(MaterialRegistry()).simulate(_lossless_task(solver))

    block = result.independence_audit
    assert block["absorption_independence_class"] == "layer_absorption_integral"
    assert block["energy_independent_max_abs_error"] < 1e-9
    assert block["energy_independent_worst_channel"] is not None
    assert block["energy_independent_worst_wavelength_nm"] is not None
    # The closure agrees on a physical lossless result: A_independent ~ A_closure ~ 0.
    closure = max(
        float(np.max(np.abs(ch["A"])))
        for ch in result.channels.values()
    )
    assert closure < 1e-9


def test_absorbing_film_matches_analytic_oracle() -> None:
    workbench = TMMWorkbench(MaterialRegistry())
    task = _film_task("smatrix")
    result = workbench.simulate(task)

    block = result.independence_audit
    assert block["absorption_independence_class"] == "layer_absorption_integral"
    assert block["energy_independent_max_abs_error"] < 1e-9

    # The oracle anchor is implementation-independent: its 1 - R - T from exact
    # Airy closed forms must match the declared channels AND, through the
    # pinned independence error above, the field-integrated absorption.
    wavelengths = result.wavelengths_nm
    max_oracle_gap = 0.0
    for angle in (0.0, 45.0):
        for pol in ("s", "p"):
            channel = result.channel(angle, pol)
            for idx, wavelength in enumerate(wavelengths):
                r_oracle, t_oracle = single_film_power_rt(
                    1.0,
                    complex(1.8, 0.4),
                    1.5,
                    60.0,
                    float(wavelength),
                    angle,
                    pol,  # type: ignore[arg-type]
                )
                a_oracle = 1.0 - r_oracle - t_oracle
                a_declared = float(channel["A"][idx])
                max_oracle_gap = max(max_oracle_gap, abs(a_declared - a_oracle))
    assert max_oracle_gap < 1e-9


def test_byrnes_absorption_fault_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    import tmm as byrnes_tmm

    original = byrnes_tmm.absorp_in_each_layer

    def inflated(data: dict) -> np.ndarray:
        values = np.asarray(original(data), dtype=np.float64).copy()
        values[1:-1] *= 1.05
        return values

    monkeypatch.setattr(byrnes_tmm, "absorp_in_each_layer", inflated)
    result = TMMWorkbench(MaterialRegistry()).simulate(_film_task("byrnes"))

    # New ledger: the field-integrated absorption no longer closes with R/T.
    assert result.independence_audit["energy_independent_max_abs_error"] > 1e-3
    # Old ledger: A = 1 - R - T makes the identity hold by construction.
    assert result.audit["energy_conservation_max_abs_error"] < 1e-12


def test_internal_absorption_fault_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    original = TMMWorkbench._layer_absorption_integrals

    def inflated(*args, **kwargs):
        return original(*args, **kwargs) * 1.05

    monkeypatch.setattr(TMMWorkbench, "_layer_absorption_integrals", staticmethod(inflated))
    result = TMMWorkbench(MaterialRegistry()).simulate(_film_task("smatrix"))

    assert result.independence_audit["energy_independent_max_abs_error"] > 1e-3
    assert result.audit["energy_conservation_max_abs_error"] < 1e-12


@pytest.mark.parametrize("injected", ["R", "T"])
def test_reflectance_fault_is_detected_on_both_ledgers(
    monkeypatch: pytest.MonkeyPatch, injected: str
) -> None:
    from tmm_engine.tmm_solver import TMM

    original = TMM.rt_spectrum

    def corrupted(self, n_list, d_list, lam, theta, pol, theta_unit="rad", **kwargs):
        r, t, amps_r, amps_t = original(
            self, n_list, d_list, lam, theta, pol, theta_unit, **kwargs
        )
        if injected == "R":
            return r * 1.02, t, amps_r, amps_t
        return r, t * 1.02, amps_r, amps_t

    monkeypatch.setattr(TMM, "rt_spectrum", corrupted)
    result = TMMWorkbench(MaterialRegistry()).simulate(_film_task("smatrix"))

    # The independent absorption integral is untouched, so the closure against
    # it breaks for both injected observables...
    assert result.independence_audit["energy_independent_max_abs_error"] > 1e-3
    # ...while the algebraic closure remains blind by construction (A is
    # *defined* as 1 - R - T from the same corrupted arrays).
    assert result.audit["energy_conservation_max_abs_error"] < 1e-12


def test_independence_degrades_honestly_without_data() -> None:
    channels = {
        "angle=0|pol=s": {
            "R": np.array([0.1, 0.2]),
            "T": np.array([0.8, 0.7]),
            "A": np.array([0.1, 0.1]),
        }
    }
    wavelengths = np.array([500.0, 600.0])

    audit = TMMWorkbench._audit_channels(channels, wavelengths)
    assert audit["absorption_independence_class"] == "unavailable"
    assert audit["energy_independent_max_abs_error"] is None
    assert audit["energy_independent_worst_channel"] is None
    assert audit["energy_independent_worst_wavelength_nm"] is None

    # Partial coverage is also degraded, never silently trusted.
    audit = TMMWorkbench._audit_channels(
        channels, wavelengths, independent_absorption={"angle=0|pol=s": np.array([0.1, 0.1])}
    )
    assert audit["absorption_independence_class"] == "layer_absorption_integral"
    assert audit["energy_independent_max_abs_error"] == pytest.approx(0.0)

    audit = TMMWorkbench._audit_channels(
        channels,
        wavelengths,
        independent_absorption={"angle=0|pol=s": None},  # type: ignore[dict-item]
    )
    assert audit["absorption_independence_class"] == "unavailable"


def test_independence_block_stays_out_of_certificate_serialization() -> None:
    workbench = TMMWorkbench(MaterialRegistry())
    result = workbench.simulate(_film_task("smatrix"))

    # result.audit feeds evidence -> certificate verbatim; the additive block
    # must keep it byte-identical to the pre-V-01 shape.
    for key in INDEPENDENCE_AUDIT_FIELDS:
        assert key not in result.audit
        assert key in result.independence_audit
    assert "independence_audit" not in result.to_dict()


def test_mixed_coherence_byrnes_independence() -> None:
    task = SimulationTask(
        stack=StackSpec(
            layers=(
                LayerSpec(None, 40.0, constant_n=1.8, constant_k=0.3),
                LayerSpec(None, 500.0, constant_n=1.5, coherence="incoherent"),
            ),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(500.0, 700.0, 21),
        illumination=IlluminationSpec((0.0,), ("s",)),
        solver="byrnes",
    )
    result = TMMWorkbench(MaterialRegistry()).simulate(task)

    assert result.independence_audit["absorption_independence_class"] == "layer_absorption_integral"
    assert result.independence_audit["energy_independent_max_abs_error"] < 1e-9
