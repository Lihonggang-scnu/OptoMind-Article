"""Cross-platform reproducibility anchors (frozen 1.1 plan).

Two honest claims, per ``tmm_engine.reproducibility`` and
``docs/VALIDATION.md``:

- **Identity level** (``byte_identical_within_scheme``): the canonical task
  identity of fixed tasks must equal the committed golden SHA-256 values on
  *any* platform, Python version, and BLAS.
- **Numerical level** (``tolerance_equivalent``): fixed simulations must
  reproduce the committed golden R/T/A values within the declared acceptance
  tolerance (the cross-solver tolerance class, 1e-7).

Bit-identical solver output is deliberately not claimed.  These tests run in
the ``cross-platform`` CI matrix jobs (Windows and macOS) and everywhere
else; they are cheap and deterministic.
"""

from __future__ import annotations

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
from tmm_engine.hashing import stable_sha256
from tmm_engine.managed_execution import normalized_operation
from tmm_engine.reproducibility import (
    IDENTITY_LEVEL,
    NUMERICAL_LEVEL,
)

NUMERICAL_TOLERANCE = 1e-7

# Golden identities generated on the reference platform under
# identity scheme veritmm-canonical-json-v1.
GOLDEN_TASK_SHA256 = {
    "dbr": "e8a914597daebb37198fcba35fd0fae3e0d67fcf49337c026e2d87c153eb3674",
    "absorbing": "55adf6820b0beaefc38feed3737855697d78b67d082c8b8ba8e530012264c10b",
}

# Golden scientific values (R/T/A per channel) for the same tasks.
GOLDEN_VALUES = {
    "dbr": {
        "angle=0|pol=s": {
            "R": [0.19348277786525758, 0.941302516906725, 0.902736563665666, 0.2664148053609643],
            "T": [0.8059681502868975, 0.05867650149683968, 0.09725900228468785, 0.7335800092164402],
            "A": [0.0005490718478449175, 2.098159643536962e-05, 4.434049646132032e-06, 5.185422595532785e-06],
        },
        "angle=30|pol=p": {
            "R": [0.007225310730865928, 0.9259087228941086, 0.7872961967696072, 0.011365632363167311],
            "T": [0.9919858571690349, 0.07406921035406863, 0.21269582433393236, 0.9886260718307048],
            "A": [0.0007888321000991683, 2.2066751822730213e-05, 7.978896460481e-06, 8.295806127822658e-06],
        },
    },
    "absorbing": {
        "angle=0|pol=s": {
            "R": [0.12339462708497045, 0.061434367184377156, 0.06716054124364115],
            "T": [0.34824314231415543, 0.438376733874715, 0.4843959228466686],
            "A": [0.5283622306008742, 0.5001888989409079, 0.44844353590969027],
        },
        "angle=45|pol=p": {
            "R": [0.05205894390105288, 0.020229643593822056, 0.020855298586220906],
            "T": [0.3385405919362047, 0.41723307290743383, 0.47040827194136914],
            "A": [0.6094004641627424, 0.5625372834987441, 0.5087364294724099],
        },
    },
}


def _dbr() -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec("tio2", 62.5), LayerSpec("sio2", 94.8)) * 4,
            incident=MediumSpec.air(),
            exit=MediumSpec("sio2"),
        ),
        spectrum=SpectralGrid(values_nm=(450.0, 550.0, 650.0, 750.0)),
        illumination=IlluminationSpec((0.0, 30.0), ("s", "p")),
    )


def _absorbing() -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(
                LayerSpec("tio2", 30.0),
                LayerSpec(None, 80.0, constant_n=1.8, constant_k=0.5),
            ),
            incident=MediumSpec.air(),
            exit=MediumSpec("sio2"),
        ),
        spectrum=SpectralGrid(values_nm=(500.0, 600.0, 700.0)),
        illumination=IlluminationSpec((0.0, 45.0), ("s", "p")),
    )


TASKS = {"dbr": _dbr, "absorbing": _absorbing}


def test_identity_level_fixed_task_hashes_match_the_golden_values() -> None:
    for name, build in TASKS.items():
        normalized = normalized_operation("simulate", build())
        assert stable_sha256(normalized) == GOLDEN_TASK_SHA256[name]


def test_numerical_level_fixed_simulations_match_within_tolerance() -> None:
    for name, build in TASKS.items():
        result = TMMWorkbench(MaterialRegistry()).simulate(build())
        golden_channels = GOLDEN_VALUES[name]
        for channel_key, golden in golden_channels.items():
            channel = result.channels[channel_key]
            for observable in ("R", "T", "A"):
                actual = [float(value) for value in channel[observable]]
                for index, expected in enumerate(golden[observable]):
                    assert actual[index] == pytest.approx(
                        expected, abs=NUMERICAL_TOLERANCE
                    ), (
                        f"{name}/{channel_key}/{observable}[{index}] deviates "
                        f"from the golden value: {actual[index]} vs {expected}"
                    )


def test_run_envelope_declares_reproducibility_levels_and_environment(
    tmp_path,
) -> None:
    output = tmp_path / "run"
    from tmm_engine.execution import ExecutionSettings, execute_task

    envelope = execute_task("simulate", _dbr(), output, settings=ExecutionSettings())
    block = envelope["reproducibility"]
    assert block["identity_level"] == IDENTITY_LEVEL
    assert block["numerical_level"] == NUMERICAL_LEVEL
    environment = block["environment"]
    for key in ("python", "numpy", "blas", "platform", "byteorder"):
        assert environment[key], f"environment fingerprint missing {key}"
    assert block["identity_scheme"] == "veritmm-canonical-json-v1"
