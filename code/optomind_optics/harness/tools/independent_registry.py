"""Explicit opt-in registration; never changes default planar TMM routing."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from .rcwa_adapter import RCWAAdapter
from .fullwave_adapter import FullwaveAdapter


@dataclass(frozen=True)
class SolverCapabilityManifest:
    solver_id: str
    version: str
    license: str
    dimension: str
    material_response: str
    excitation: str
    boundaries: str
    convergence_axes: tuple[str, ...]
    observables: tuple[str, ...] = ('R','T','diffraction_orders','complex_amplitudes')
    supports_gradients: bool = False
    supports_cancellation: bool = False
    physical_evidence: bool = False


MANIFESTS = (
    SolverCapabilityManifest('grcwa','0.1.2','GPL-3.0','2D periodic transverse lattice / layered z',
        'positive real scalar epsilon, fixed frequency','s or p plane wave, |theta| < 85 deg',
        'periodic xy, uniform lossless incident/exit ports',('Fourier truncation','epsilon sampling','layer slicing')),
    SolverCapabilityManifest('ceviche','0.1.3','MIT','2D Ez',
        'real scalar epsilon >= 1, fixed frequency','normal incidence Ez line current, empty-domain normalization',
        'PML x / periodic y, vacuum ports',('dx','PML thickness','domain size','linear residual')),
)


def register_independent_tools(registry, receipt_dir=None):
    """Caller chooses optional dependencies and license policy; missing modules fail preflight."""
    from pathlib import Path
    result=[]
    for cls in (RCWAAdapter,FullwaveAdapter):
        path=Path(receipt_dir)/cls.backend if receipt_dir is not None else None
        adapter=cls(path)
        registry.register(adapter)
        result.append(adapter)
    return result


def manifest_dicts():
    return [asdict(m) for m in MANIFESTS]
