"""Analytic validation oracle suite: engine and referee vs exact closed forms.

The oracle (``tmm_engine.analytic_oracle``) implements Fresnel/Airy physics
from first principles, independent of every engine solver.  These tests are
the CI anchor layer from the frozen 1.1 plan — they run in the test suite
only and never touch the runtime certificate path (asserted by the isolation
test below).

Tolerances are set for float64 agreement with well-conditioned analytic
values; the mpmath referee is additionally anchored against the same oracle
when it is installed.
"""

from __future__ import annotations

from pathlib import Path

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
from tmm_engine.analytic_oracle import (
    fabry_perot_resonance_wavelengths,
    interface_power_rt,
    quarter_wave_stack_band_edges,
    quarter_wave_stack_reflectance_at_design,
    single_film_power_rt,
)

TOLERANCE = 1e-9


def _simulate_rta(
    layers: tuple,
    wavelength: float,
    angle: float,
    polarization: str,
    n_incident: float = 1.0,
    n_exit: float = 1.45,
) -> tuple[float, float, float]:
    task = SimulationTask(
        stack=StackSpec(
            layers=layers,
            incident=MediumSpec(constant_n=n_incident),
            exit=MediumSpec(constant_n=n_exit),
        ),
        spectrum=SpectralGrid(values_nm=(wavelength,)),
        illumination=IlluminationSpec((angle,), (polarization,)),
    )
    result = TMMWorkbench(MaterialRegistry()).simulate(task)
    channel = result.channels[f"angle={angle:g}|pol={polarization}"]
    return (
        float(channel["R"][0]),
        float(channel["T"][0]),
        float(channel["A"][0]),
    )


def test_oracle_internal_consistency_thin_film_limit() -> None:
    """A film whose thickness tends to zero must become its interface."""

    for angle in (0.0, 30.0, 50.0):
        for polarization in ("s", "p"):
            interface = interface_power_rt(1.0, 1.45, angle, polarization)
            limit = single_film_power_rt(
                1.0, 1.5, 1.45, 1e-7, 550.0, angle, polarization
            )
            assert limit == pytest.approx(interface, rel=1e-9)


def test_engine_matches_fresnel_interface_through_the_thin_film_limit(
    tmp_path: Path,
) -> None:
    for angle in (0.0, 30.0, 50.0):
        for polarization in ("s", "p"):
            layers = (LayerSpec(None, 1e-4, constant_n=1.5),)
            engine_r, engine_t, engine_a = _simulate_rta(
                layers, 550.0, angle, polarization
            )
            oracle_r, oracle_t = interface_power_rt(1.0, 1.45, angle, polarization)
            assert engine_r == pytest.approx(oracle_r, rel=TOLERANCE)
            assert engine_t == pytest.approx(oracle_t, rel=TOLERANCE)
            assert engine_a == pytest.approx(1.0 - oracle_r - oracle_t, abs=1e-9)


@pytest.mark.parametrize("angle", [0.0, 30.0, 50.0])
@pytest.mark.parametrize("polarization", ["s", "p"])
def test_engine_matches_the_airy_formula_lossless_film(
    angle: float, polarization: str
) -> None:
    layers = (LayerSpec(None, 120.0, constant_n=2.2),)
    engine_r, engine_t, engine_a = _simulate_rta(layers, 550.0, angle, polarization)
    oracle_r, oracle_t = single_film_power_rt(
        1.0, 2.2, 1.45, 120.0, 550.0, angle, polarization
    )
    assert engine_r == pytest.approx(oracle_r, rel=TOLERANCE)
    assert engine_t == pytest.approx(oracle_t, rel=TOLERANCE)
    assert engine_a == pytest.approx(1.0 - oracle_r - oracle_t, abs=1e-9)


@pytest.mark.parametrize("angle", [0.0, 45.0])
@pytest.mark.parametrize("polarization", ["s", "p"])
def test_engine_matches_the_airy_formula_absorbing_film(
    angle: float, polarization: str
) -> None:
    """Absorbing films are exactly the class the 1.1 sign bug corrupted.

    The engine expresses absorption through ``constant_k`` (index convention
    n + i*k, k positive); the oracle uses the equivalent complex index.
    """

    layers = (LayerSpec(None, 80.0, constant_n=1.8, constant_k=0.5),)
    engine_r, engine_t, engine_a = _simulate_rta(layers, 550.0, angle, polarization)
    oracle_r, oracle_t = single_film_power_rt(
        1.0, 1.8 + 0.5j, 1.45, 80.0, 550.0, angle, polarization
    )
    assert engine_r == pytest.approx(oracle_r, rel=TOLERANCE)
    assert engine_t == pytest.approx(oracle_t, rel=TOLERANCE)
    assert engine_a == pytest.approx(1.0 - oracle_r - oracle_t, abs=1e-9)
    assert oracle_r + oracle_t < 1.0  # passivity on the analytic side too


def test_engine_matches_the_quarter_wave_closed_form_at_design() -> None:
    n0, n_h, n_l, n_s = 1.0, 2.2, 1.45, 1.52
    design = 550.0
    periods = 8
    # Quarter-wave *optical* thickness: physical thickness = design / (4 n).
    layers = (
        LayerSpec(None, design / (4.0 * n_h), constant_n=n_h),
        LayerSpec(None, design / (4.0 * n_l), constant_n=n_l),
    ) * periods
    engine_r, _engine_t, _engine_a = _simulate_rta(
        layers, design, 0.0, "s", n_incident=n0, n_exit=n_s
    )
    oracle_r = quarter_wave_stack_reflectance_at_design(
        n0, n_h, n_l, n_s, periods, design
    )
    assert engine_r == pytest.approx(oracle_r, rel=TOLERANCE)
    assert oracle_r > 0.99


def test_quarter_wave_stopband_edges_bound_the_high_reflectance_region() -> None:
    n0, n_h, n_l, n_s = 1.0, 2.2, 1.45, 1.52
    design = 550.0
    periods = 12
    layers = (
        LayerSpec(None, design / (4.0 * n_h), constant_n=n_h),
        LayerSpec(None, design / (4.0 * n_l), constant_n=n_l),
    ) * periods
    short_edge, long_edge = quarter_wave_stack_band_edges(n_h, n_l, design)
    assert short_edge < design < long_edge

    mid = _simulate_rta(layers, design, 0.0, "s", n_incident=n0, n_exit=n_s)[0]
    outside_long = _simulate_rta(
        layers, long_edge * 1.3, 0.0, "s", n_incident=n0, n_exit=n_s
    )[0]
    outside_short = _simulate_rta(
        layers, short_edge * 0.7, 0.0, "s", n_incident=n0, n_exit=n_s
    )[0]
    assert mid > 0.99
    assert outside_long < 0.2
    assert outside_short < 0.2


def test_engine_transmission_is_unity_at_fabry_perot_resonances() -> None:
    n_film, n_surrounding = 1.5, 1.0
    thickness = 1000.0
    for angle in (0.0, 30.0):
        for order in (1, 2, 3):
            resonance = fabry_perot_resonance_wavelengths(
                n_film, n_surrounding, thickness, angle, (order,)
            )[0]
            layers = (LayerSpec(None, thickness, constant_n=n_film),)
            engine_r, engine_t, engine_a = _simulate_rta(
                layers, resonance, angle, "s", n_incident=1.0, n_exit=1.0
            )
            assert engine_t == pytest.approx(1.0, abs=1e-9)
            assert engine_r == pytest.approx(0.0, abs=1e-9)
            assert engine_a == pytest.approx(0.0, abs=1e-9)


def test_engine_energy_balance_matches_the_oracle_off_resonance() -> None:
    layers = (LayerSpec(None, 300.0, constant_n=1.5),)
    wavelength = 480.0
    engine_r, engine_t, engine_a = _simulate_rta(layers, wavelength, 40.0, "p")
    oracle_r, oracle_t = single_film_power_rt(
        1.0, 1.5, 1.45, 300.0, wavelength, 40.0, "p"
    )
    assert engine_a == pytest.approx(1.0 - oracle_r - oracle_t, abs=1e-9)
    assert engine_r == pytest.approx(oracle_r, rel=TOLERANCE)


def test_analytic_oracle_is_never_imported_by_runtime_modules() -> None:
    """CI-only positioning: the oracle must stay out of the runtime path."""

    engine_root = Path(__file__).resolve().parents[1] / "tmm_engine"
    offenders = []
    for path in engine_root.rglob("*.py"):
        if path.name == "analytic_oracle.py" or "__pycache__" in path.parts:
            continue
        if "analytic_oracle" in path.read_text(encoding="utf-8-sig"):
            offenders.append(str(path.relative_to(engine_root)))
    assert offenders == []


class TestHighPrecisionRefereeAgainstOracle:
    """The mpmath referee is anchored against the same analytic closed forms."""

    @pytest.fixture(autouse=True)
    def _require_referee(self):
        pytest.importorskip("mpmath")

    @pytest.mark.parametrize("angle", [0.0, 45.0])
    @pytest.mark.parametrize("polarization", ["s", "p"])
    def test_referee_matches_the_oracle_on_an_absorbing_film(
        self, angle: float, polarization: str
    ) -> None:
        from tmm_engine.high_precision import compute_rt_single_channel

        n_all = [1.0 + 0j, 1.8 + 0.5j, 1.45 + 0j]
        result = compute_rt_single_channel(
            n_all=n_all,
            d_nm=[80.0],
            wavelengths_nm=[400.0, 550.0, 700.0],
            angle_deg=angle,
            polarization=polarization,
        )
        assert result["status"] == "ok"
        oracle_r, oracle_t = None, None
        for index, wavelength in enumerate((400.0, 550.0, 700.0)):
            oracle_r, oracle_t = single_film_power_rt(
                1.0, 1.8 + 0.5j, 1.45, 80.0, wavelength, angle, polarization
            )
            assert float(result["R"][index]) == pytest.approx(oracle_r, rel=1e-9)
            assert float(result["T"][index]) == pytest.approx(oracle_t, rel=1e-9)
            assert float(result["R"][index]) + float(result["T"][index]) <= 1.0 + 1e-12
