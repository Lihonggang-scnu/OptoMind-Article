"""Cross-backend equivalence for the unified physics backend registry.

The same KernelSpec (a plain, array-library-agnostic description of one stack
assembly) must produce matching R/T on the numpy reference backend (wrapping
``tmm_engine.tmm_solver``) and the torch differentiable backend (wrapping
``tmm_engine.differentiable.DifferentiableTMM``).  Adding a backend is a
drop-in ``*_backend.py`` file — the registry discovers it and reports absent
optional dependencies as not-registered, never as import errors.
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from tmm_engine.backends import (  # noqa: E402
    BackendNotRegisteredError,
    KernelSpec,
    available_backends,
    get_backend,
    register_backend_search_path,
)

ENGINE_ROOT = Path(__file__).resolve().parents[1] / "tmm_engine"
REGISTRY_MODULE = ENGINE_ROOT / "backends" / "registry.py"

WAVELENGTHS = np.linspace(450.0, 750.0, 31)


def _flat_nk(n_values, wavelengths):
    return tuple(np.full(wavelengths.shape, n, dtype=np.complex128) for n in n_values)


def _kernel(n_values, thicknesses_nm, angle_deg, polarization) -> KernelSpec:
    return KernelSpec(
        nk_stack=_flat_nk(n_values, WAVELENGTHS),
        thicknesses_nm=thicknesses_nm,
        wavelengths_nm=WAVELENGTHS.copy(),
        angle_deg=angle_deg,
        polarization=polarization,
    )


def _cross_backend_r_t(kernel: KernelSpec) -> tuple:
    reference = get_backend("numpy").assemble(kernel)
    differentiable = get_backend("torch").assemble(kernel)
    return reference, differentiable


@pytest.mark.parametrize(
    "kernel",
    [
        _kernel([1.0, 2.4, 1.46, 2.4, 1.46, 2.4, 1.46, 1.52],
                [91.0, 137.0, 91.0, 137.0, 91.0, 137.0], 31.0, "s"),
        _kernel([1.0, 2.4, 1.46, 2.4, 1.46, 2.4, 1.46, 1.52],
                [91.0, 137.0, 91.0, 137.0, 91.0, 137.0], 31.0, "p"),
        _kernel([1.0, 2.1 + 0.4j, 1.5], [100.0], 0.0, "p"),
        _kernel([1.0, 2.1 + 0.4j, 1.5], [100.0], 40.0, "s"),
        _kernel([1.0, 2.1 + 0.4j, 1.5], [100.0], 40.0, "p"),
        _kernel([1.0, 2.1 + 0.4j, 1.5], [100.0], 60.0, "s"),
    ],
    ids=[
        "lossless-dbr-s",
        "lossless-dbr-p",
        "absorbing-film-normal-p",
        "absorbing-film-oblique-s",
        "absorbing-film-oblique-p",
        "absorbing-film-steep-s",
    ],
)
def test_backends_agree_on_r_and_t(kernel: KernelSpec) -> None:
    reference, differentiable = _cross_backend_r_t(kernel)
    np.testing.assert_allclose(
        differentiable.R.detach().numpy()[0], reference.R, rtol=1e-9, atol=1e-12
    )
    np.testing.assert_allclose(
        differentiable.T.detach().numpy()[0], reference.T, rtol=1e-9, atol=1e-12
    )


def test_torch_float32_tier_is_consistent_within_its_dtype() -> None:
    """The float32 tier trades precision for memory/speed: a looser tolerance
    applies by dtype semantics, and the tier is opt-in per assemble call."""

    kernel = _kernel([1.0, 2.4, 1.46, 1.52], [91.0, 137.0], 31.0, "s")
    result = get_backend("torch").assemble(kernel, dtype="float32")
    reference = get_backend("numpy").assemble(kernel)

    assert result.R.dtype == torch.float32
    np.testing.assert_allclose(
        result.R.detach().numpy()[0], reference.R, rtol=1e-4, atol=1e-5
    )


def test_torch_gradient_matches_central_difference() -> None:
    centers = (100.0, 60.0)
    thicknesses = torch.tensor([list(centers)], dtype=torch.float64, requires_grad=True)
    kernel = KernelSpec(
        nk_stack=_flat_nk([1.0, 2.1 + 0.4j, 1.5 + 0.05j, 1.5], WAVELENGTHS[::2]),
        thicknesses_nm=thicknesses,
        wavelengths_nm=WAVELENGTHS[::2].copy(),
        angle_deg=30.0,
        polarization="s",
    )
    torch_backend = get_backend("torch")
    torch_backend.assemble(kernel).R.sum().backward()
    autograd = thicknesses.grad.detach().numpy()[0]

    step = 1e-4

    def reflectance(shifts) -> float:
        shifted = KernelSpec(
            nk_stack=kernel.nk_stack,
            thicknesses_nm=[[center + shift for center, shift in zip(centers, shifts)]],
            wavelengths_nm=kernel.wavelengths_nm,
            angle_deg=kernel.angle_deg,
            polarization=kernel.polarization,
        )
        return float(torch_backend.assemble(shifted).R.sum())

    for index, gradient in enumerate(autograd):
        plus = [0.0, 0.0]
        minus = [0.0, 0.0]
        plus[index] = step
        minus[index] = -step
        central = (reflectance(plus) - reflectance(minus)) / (2.0 * step)
        assert gradient == pytest.approx(central, rel=1e-6, abs=1e-9)


def test_unregistered_backend_reports_not_registered() -> None:
    assert "jax" not in available_backends()
    with pytest.raises(BackendNotRegisteredError) as excinfo:
        get_backend("jax")
    assert not isinstance(excinfo.value, ImportError)
    assert "numpy" in str(excinfo.value)


def test_new_backend_is_a_drop_in_file(tmp_path: Path) -> None:
    backend_file = tmp_path / "probe_backend.py"
    backend_file.write_text(
        "BACKEND_NAME = 'probe'\n"
        "def assemble(kernel):\n"
        "    from tmm_engine.backends.registry import BackendResult\n"
        "    return BackendResult(R=0.0, T=0.0, A=1.0)\n",
        encoding="utf-8",
    )
    import tmm_engine.backends.registry as registry

    register_backend_search_path(tmp_path)
    try:
        assert "probe" in available_backends()
        kernel = _kernel([1.0, 2.1, 1.5], [100.0], 0.0, "s")
        assert get_backend("probe").assemble(kernel).A == 1.0
    finally:
        registry._extra_search_paths.remove(tmp_path)
        registry._discovered.pop("probe", None)
        registry._discovered.clear()


def test_registry_container_is_array_library_agnostic() -> None:
    """KernelSpec and the registry must not import numpy or torch: the same
    description is consumed by every backend natively."""

    tree = ast.parse(REGISTRY_MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert "numpy" not in imported
    assert "torch" not in imported


def test_numpy_reference_backend_matches_the_workbench_path() -> None:
    """The reference backend wraps the existing solver path without changing
    its numerics: identical inputs produce identical arrays."""

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

    kernel = _kernel([1.0, 2.15, 1.43, 1.52], [91.0, 137.0], 31.0, "unpolarized")
    backend_result = get_backend("numpy").assemble(kernel)

    task = SimulationTask(
        stack=StackSpec(
            layers=(
                LayerSpec(None, 91.0, constant_n=2.15),
                LayerSpec(None, 137.0, constant_n=1.43),
            ),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.52),
        ),
        spectrum=SpectralGrid(values_nm=tuple(WAVELENGTHS.tolist())),
        illumination=IlluminationSpec(angles_deg=(31.0,), polarizations=("unpolarized",)),
    )
    channel = TMMWorkbench(MaterialRegistry()).simulate(task).channel(31.0, "unpolarized")
    np.testing.assert_array_equal(backend_result.R, channel["R"])
    np.testing.assert_array_equal(backend_result.T, channel["T"])
