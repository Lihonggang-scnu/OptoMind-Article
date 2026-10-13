"""Ceviche 0.1.3 FDFD Ez adapter, not a new electromagnetic engine.

2D (x propagation, y periodic), real isotropic epsilon, normal incidence,
vacuum ports, x PML. A second empty-domain solve normalizes the line current.
Discrete-dispersion Fourier decomposition separates incoming/outgoing modes.
No FDTD, oblique incidence, absorption, dispersion, gradients, or 3D claims.
"""
from __future__ import annotations
import math
import numpy as np
from .numerical_base import NumericalAdapter, complex_pairs


class FullwaveAdapter(NumericalAdapter):
    backend = "ceviche"
    backend_version = "0.1.3"
    capability_id = "fullwave.fdfd_ez_2d"
    license = "MIT"

    def validate(self, p):
        allowed = {"length_unit", "wavelength_nm", "dx_nm", "epsilon_grid", "npml", "source_x",
                   "reflection_x", "transmission_x", "source_amplitude", "theta_deg", "polarization"}
        if set(p) - allowed:
            raise ValueError("unsupported request fields: " + str(sorted(set(p)-allowed)))
        if p.get("length_unit", "nm") != "nm":
            raise ValueError("length_unit must be nm")
        if p.get("theta_deg", 0) != 0 or p.get("polarization", "Ez") != "Ez":
            raise ValueError("only normal incidence Ez polarization supported")
        for name in ("wavelength_nm", "dx_nm"):
            if not math.isfinite(p[name]) or p[name] <= 0:
                raise ValueError(name + " must be finite positive")
        eps = np.asarray(p["epsilon_grid"], dtype=complex)
        if eps.ndim != 2 or min(eps.shape) < 3 or eps.size > 100000:
            raise ValueError("bounded 2D epsilon grid required (<=100000 cells)")
        if np.any(~np.isfinite(eps)) or np.any(eps.imag != 0) or np.min(eps.real) < 1:
            raise ValueError("only real isotropic epsilon >= 1 supported")
        if p["wavelength_nm"] / (p["dx_nm"] * np.sqrt(eps.real.max())) < 10:
            raise ValueError("at least 10 grid points per shortest material wavelength required")
        nx, ny = eps.shape
        for name in ("npml", "source_x", "reflection_x", "transmission_x"):
            if not isinstance(p[name], int):
                raise ValueError(name + " must be integer")
        n, s, r, t = (p[k] for k in ("npml", "source_x", "reflection_x", "transmission_x"))
        if not 6 <= n < s < r < t < nx - n - 2:
            raise ValueError("require 6 <= npml < source < reflection < transmission < right PML")
        if not np.all(eps[:r + 2] == 1) or not np.all(eps[t:] == 1):
            raise ValueError("vacuum source, monitors and PML regions required")
        if p.get("source_amplitude", 1) == 0 or not np.isfinite(p.get("source_amplitude", 1)):
            raise ValueError("finite nonzero source amplitude required")

    @staticmethod
    def _modes(E, x, kd, ny):
        ky = 2 * np.pi * np.fft.fftfreq(ny)
        sine2 = (kd / 2)**2 - np.sin(ky / 2)**2
        # Exclude evanescent channels; close cutoffs require a separately shifted run.
        if np.min(np.abs(sine2)) < 1e-10:
            raise ValueError("Rayleigh cutoff too close for stable power decomposition")
        propagating = (sine2 > 0) & (sine2 < 1)
        q = 2 * np.arcsin(np.sqrt(sine2[propagating]))
        e = np.fft.fft(E[x]) / ny
        f = np.fft.fft(E[x + 1]) / ny
        # Ceviche uses exp(+i omega t), so forward is exp(-i k x).
        backward = (f[propagating] - e[propagating] * np.exp(-1j*q)) / (2j*np.sin(q))
        forward = e[propagating] - backward
        return forward, backward, np.sin(q), np.fft.fftfreq(ny, 1/ny)[propagating].astype(int)

    def solve(self, p):
        self.validate(p)
        from ceviche import fdfd_ez
        from ceviche.constants import C_0
        from scipy.sparse import coo_matrix
        eps = np.asarray(p["epsilon_grid"], dtype=float)
        nx, ny = eps.shape
        omega = 2 * np.pi * C_0 / (p["wavelength_nm"] * 1e-9)
        source = np.zeros_like(eps, dtype=complex)
        source[p["source_x"], :] = p.get("source_amplitude", 1)
        solver = fdfd_ez(omega, p["dx_nm"] * 1e-9, eps, [p["npml"], 0])
        hx, hy, E = solver.solve(source)
        reference = fdfd_ez(omega, p["dx_nm"] * 1e-9, np.ones_like(eps), [p["npml"], 0])
        E0 = reference.solve(source)[2]
        kd = 2 * np.pi * p["dx_nm"] / p["wavelength_nm"]
        incident, reference_backward, weight, orders = self._modes(E0, p["reflection_x"], kd, ny)
        _, reflected, _, _ = self._modes(E, p["reflection_x"], kd, ny)
        transmitted, incoming_right, _, _ = self._modes(E, p["transmission_x"], kd, ny)
        norm = np.sum(weight * abs(incident)**2)
        if not np.isfinite(norm) or norm <= 0:
            raise ValueError("invalid incident source normalization")
        rp = weight * abs(reflected - reference_backward)**2 / norm
        tp = weight * abs(transmitted)**2 / norm
        entries, indices = solver._make_A(eps.ravel())
        A = coo_matrix((entries, indices), shape=(eps.size, eps.size)).tocsr()
        b = 1j * omega * source.ravel()
        residual = float(np.linalg.norm(A @ E.ravel() - b) / np.linalg.norm(b))
        R, T = float(np.sum(rp)), float(np.sum(tp))
        return {"R": R, "T": T, "energy_balance_error": abs(1-R-T),
                "linear_residual": residual, "orders": orders.tolist(),
                "R_orders": rp.tolist(), "T_orders": tp.tolist(),
                "reflected_amplitudes": complex_pairs((reflected-reference_backward)/incident[0]),
                "transmitted_amplitudes": complex_pairs(transmitted/incident[0]),
                "amplitude_convention": "Ez at monitor planes, exp(+i omega t); vacuum source normalized",
                "reference_backreflection": float(np.sum(weight*abs(reference_backward)**2)/norm),
                "right_incoming_power": float(np.sum(weight*abs(incoming_right)**2)/norm),
                "field_Ez": complex_pairs(E), "grid_shape": [nx, ny], "dx_nm": p["dx_nm"],
                "npml": p["npml"], "wavelength_nm": p["wavelength_nm"], "theta_deg": 0,
                "boundary": "PML x / periodic y", "solver_calls": 2,
                "validation_scope": "lossless 2D Ez normal incidence vacuum ports; grid/PML convergence required"}
