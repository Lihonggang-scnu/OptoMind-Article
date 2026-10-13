"""grcwa 0.1.2 thin adapter: lossless isotropic periodic slabs, explicit nm units.

Complex amplitudes are grcwa's magnetic Fourier-mode coefficients, not electric
Fresnel amplitudes. Exterior reference planes are the first/last interfaces.
GPL-3.0 optional dependency; do not bundle without distribution license review.
"""
from __future__ import annotations
import math
import numpy as np
from .numerical_base import NumericalAdapter, complex_pairs


class RCWAAdapter(NumericalAdapter):
    backend = "grcwa"
    backend_version = "0.1.2"
    capability_id = "rcwa.periodic_2d"
    license = "GPL-3.0"

    def validate(self, p):
        if p.get("length_unit", "nm") != "nm":
            raise ValueError("length_unit must be nm")
        for name in ("wavelength_nm", "n_incident", "n_exit"):
            value = float(p.get(name, 1.0) if name != "wavelength_nm" else p[name])
            if not math.isfinite(value) or value <= 0:
                raise ValueError(name + " must be finite positive")
        allowed = {"length_unit", "wavelength_nm", "period_nm", "theta_deg", "phi_deg", "polarization",
                   "n_incident", "n_exit", "layers", "fourier_order"}
        if set(p) - allowed:
            raise ValueError("unsupported request fields: " + str(sorted(set(p)-allowed)))
        period = np.asarray(p["period_nm"], dtype=float)
        if period.shape != (2,) or not np.all(np.isfinite(period)) or np.min(period) <= 0:
            raise ValueError("period_nm must contain two positive lengths")
        if p.get("polarization", "s") not in ("s", "p"):
            raise ValueError("polarization must be s or p")
        if not np.isfinite(p.get("theta_deg", 0)) or abs(p.get("theta_deg", 0)) >= 85:
            raise ValueError("theta_deg outside supported (-85,85)")
        if not np.isfinite(p.get("phi_deg", 0)):
            raise ValueError("phi_deg must be finite")
        # Physical exterior Rayleigh boundaries are excluded, independently of truncation.
        k0 = 2*np.pi/p["wavelength_nm"]
        theta, phi = np.deg2rad(p.get("theta_deg",0)), np.deg2rad(p.get("phi_deg",0))
        kx = k0*p.get("n_incident",1)*np.sin(theta)*np.cos(phi)
        ky = k0*p.get("n_incident",1)*np.sin(theta)*np.sin(phi)
        for m in range(-10,11):
            for n in range(-10,11):
                kt2 = (kx+2*np.pi*m/period[0])**2 + (ky+2*np.pi*n/period[1])**2
                for exterior in (p.get("n_incident",1),p.get("n_exit",1)):
                    if abs(kt2-(k0*exterior)**2) < 1e-10*k0*k0:
                        raise ValueError("Rayleigh cutoff too close; offset wavelength or angle")
        order = p.get("fourier_order", 21)
        if not isinstance(order, int) or not 3 <= order <= 101:
            raise ValueError("fourier_order integer in [3,101] required")
        if not 1 <= len(p["layers"]) <= 32:
            raise ValueError("one to 32 layers required")
        for layer in p["layers"]:
            if not np.isfinite(layer["thickness_nm"]) or layer["thickness_nm"] <= 0:
                raise ValueError("positive finite thickness_nm required")
            if ("epsilon" in layer) == ("epsilon_grid" in layer):
                raise ValueError("exactly one epsilon representation required")
            eps = np.asarray(layer.get("epsilon_grid", layer.get("epsilon")), dtype=complex)
            if np.any(~np.isfinite(eps)) or np.any(eps.imag != 0) or np.any(eps.real <= 0):
                raise ValueError("only positive real isotropic permittivity is supported")
            if "epsilon_grid" in layer and (eps.ndim != 2 or min(eps.shape) < 2 or eps.size > 65536):
                raise ValueError("epsilon_grid must be bounded 2D grid")
            if "epsilon" in layer and eps.ndim != 0:
                raise ValueError("epsilon must be scalar")

    def solve(self, p):
        self.validate(p)
        import grcwa
        # Set backend explicitly; automatic differentiation is not advertised.
        grcwa.set_backend("numpy")
        lx, ly = p["period_nm"]
        obj = grcwa.obj(p.get("fourier_order", 21), [lx, 0], [0, ly],
                        1 / p["wavelength_nm"], np.deg2rad(p.get("theta_deg", 0)),
                        np.deg2rad(p.get("phi_deg", 0)), verbose=0)
        obj.Add_LayerUniform(0, p.get("n_incident", 1.0)**2)
        grids = []
        for layer in p["layers"]:
            if "epsilon_grid" in layer:
                grid = np.asarray(layer["epsilon_grid"], dtype=float)
                obj.Add_LayerGrid(layer["thickness_nm"], *grid.shape)
                grids.append(grid.ravel())
            else:
                obj.Add_LayerUniform(layer["thickness_nm"], layer["epsilon"])
        obj.Add_LayerUniform(0, p.get("n_exit", 1.0)**2)
        obj.Init_Setup()
        if grids:
            # Explicitly reject undersampled Fourier support rather than indexing incorrectly.
            for layer in p["layers"]:
                if "epsilon_grid" in layer:
                    shape = np.shape(layer["epsilon_grid"])
                    if any(2 * np.max(np.abs(obj.G[:, i])) >= shape[i] for i in (0, 1)):
                        raise ValueError("epsilon grid undersamples Fourier support")
            obj.GridLayer_geteps(np.concatenate(grids))
        pol = p.get("polarization", "s")
        obj.MakeExcitationPlanewave(int(pol == "p"), 0, int(pol == "s"), 0, order=0)
        # Rayleigh cutoffs are singular limits, not evidence of failed conservation.
        cutoff = min(float(np.min(np.abs(obj.q_list[i]))) for i in (0, -1))
        if cutoff < (2 * np.pi / p["wavelength_nm"]) * 1e-8:
            raise ValueError("Rayleigh cutoff too close; offset wavelength or angle")
        r, t = obj.RT_Solve(normalize=1, byorder=1)
        _, reflected = obj.GetAmplitudes(0, 0)
        transmitted, _ = obj.GetAmplitudes(obj.Layer_N - 1, 0)
        R, T = float(np.sum(r)), float(np.sum(t))
        if not np.all(np.isfinite([R, T])):
            raise ValueError("nonfinite solver result")
        return {"R": R, "T": T, "energy_balance_error": abs(1 - R - T),
                "orders": np.asarray(obj.G).tolist(), "R_orders": np.asarray(r).tolist(),
                "T_orders": np.asarray(t).tolist(), "reflected_amplitudes": complex_pairs(reflected),
                "transmitted_amplitudes": complex_pairs(transmitted),
                "amplitude_convention": "grcwa magnetic Fourier coefficients, exp(-i omega t)",
                "actual_fourier_order": int(obj.nG), "requested_fourier_order": p.get("fourier_order", 21),
                "wavelength_nm": p["wavelength_nm"], "theta_deg": p.get("theta_deg", 0),
                "validation_scope": "lossless periodic slab; convergence must be checked separately"}
