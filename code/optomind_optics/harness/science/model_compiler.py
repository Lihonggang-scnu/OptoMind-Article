"""Thin optical IR adapter preserving the production VeriTMM solver."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping
import numpy as np
from .model_ir import CompileError, ModelIR, OPERATORS
from .compute_ledger import meter_prediction, computational_event


@dataclass(frozen=True)
class CompiledModel:
    ir: ModelIR
    # Deterministic forward operators do not themselves define a likelihood.
    probability_support: bool = False

    @property
    def parameter_specs(self):
        return self.ir.parameters

    @property
    def model_id(self):
        return self.ir.structural_hash

    @meter_prediction
    def predict(self, parameters: Mapping[str, float], wavelengths_nm, *, angle_deg=0., channel="R"):
        wl = np.asarray(wavelengths_nm, dtype=float)
        if wl.ndim != 1 or not 1 <= len(wl) <= 4096 or not np.all(np.isfinite(wl)) or np.any(np.diff(wl) <= 0):
            raise ValueError("wavelengths must be finite, nonempty and strictly increasing")
        if wl.min() < self.ir.domain_nm[0] or wl.max() > self.ir.domain_nm[1]:
            raise CompileError("OUTSIDE_DOMAIN", "wavelength outside model validity")
        if not 0 <= angle_deg < 90 or channel not in ("R", "T", "A"):
            raise CompileError("INVALID_CHANNEL", "requires external angle and R/T/A intensity")
        unknown = set(parameters) - {p.name for p in self.ir.parameters}
        if unknown:
            raise ValueError(f"unknown model parameters: {sorted(unknown)}")
        pars = {p.name: parameters.get(p.name, p.initial) for p in self.ir.parameters}
        for p in self.ir.parameters:
            if not np.isfinite(pars[p.name]) or not p.lower <= pars[p.name] <= p.upper:
                raise ValueError(f"parameter outside bounds: {p.name}")
        def val(x):
            return pars[x[1:]] if isinstance(x, str) and x.startswith("$") else x
        values = {}
        for n in self.ir.nodes:
            a = {k: val(v) for k, v in n.attributes}
            inputs = [values[i] for i in n.inputs]
            if n.operator in ("constant_nk", "cauchy_nk"):
                real = a.get("n", a.get("A", 1.))
                if n.operator == "cauchy_nk":
                    real = real + a.get("B_um2", 0.) / (wl / 1000.) ** 2 + a.get("C_um4", 0.) / (wl / 1000.) ** 4
                k = a.get("k", 0.)
                if np.any(np.asarray(real) <= 0) or k < 0:
                    raise CompileError("NONPASSIVE_MEDIUM", "requires positive index and nonnegative extinction")
                result = np.broadcast_to(real + 1j * k, wl.shape)
            elif n.operator in ("layer", "linear_profile"):
                thickness = a["thickness"] + a.get("shared_bias", 0.)
                if n.unit == "um":
                    thickness *= 1000
                if thickness < 0:
                    raise CompileError("NEGATIVE_THICKNESS", n.node_id)
                coherence = a.get("coherence", "coherent")
                if coherence not in ("coherent", "incoherent"):
                    raise CompileError("COHERENCE_TYPE", str(coherence))
                if n.operator == "linear_profile":
                    segments = a.get("segments", 16)
                    if not isinstance(segments, int) or not 1 <= segments <= 4096:
                        raise CompileError("RESOURCE_LIMIT", "profile segments must be 1..4096")
                    result = tuple((inputs[0] + ((j + .5) / segments) * (inputs[1] - inputs[0]), thickness / segments, coherence) for j in range(segments))
                else:
                    result = ((inputs[0], thickness, coherence),)
            elif n.operator == "stack":
                result = (tuple(layer for group in inputs[:-1] for layer in group), inputs[-1])
            elif n.operator == "tmm":
                from tmm_engine.tmm_solver import TMM, TMMConfig
                layers, substrate = inputs[0]
                if len(layers)>4096:raise CompileError("RESOURCE_LIMIT", "compiled physical layer count exceeds 4096")
                solver = TMM(TMMConfig(wavelength_unit="um", n_incident=a.get("n_incident", 1.)))
                aperture = a.get("aperture_deg", 0.)
                if aperture < 0:
                    raise ValueError("aperture must be nonnegative")
                points = a.get("quadrature_points", 5)
                if isinstance(points,bool) or not isinstance(points,int) or not 1 <= points <= 64:
                    raise CompileError("RESOURCE_LIMIT", "quadrature_points must be an integer in 1..64")
                offsets, weights = np.polynomial.legendre.leggauss(points) if aperture else (np.array([0.]), np.array([2.]))
                s_fraction = a.get("s_fraction", 0.5)
                if not 0 <= s_fraction <= 1:
                    raise ValueError("s_fraction must lie in [0,1]")
                result = np.zeros_like(wl)
                for offset, weight in zip(offsets, weights / 2):
                    angle = angle_deg + offset * aperture
                    if not -90 < angle < 90:
                        raise CompileError("ANGLE_DOMAIN", "aperture reaches grazing incidence")
                    for pol, mix in (("s", s_fraction), ("p", 1 - s_fraction)):
                        if not mix:
                            continue
                        if any(v[2] == "incoherent" for v in layers):
                            from tmm import inc_tmm
                            rs, ts = [], []
                            for j, wavelength in enumerate(wl):
                                with computational_event("solver_points", 1):
                                    data = inc_tmm(pol, [a.get("n_incident", 1.)] + [complex(v[0][j]) for v in layers] + [complex(substrate[j])], [np.inf] + [v[1] for v in layers] + [np.inf], ["i"] + ["c" if v[2] == "coherent" else "i" for v in layers] + ["i"], np.deg2rad(angle), wavelength)
                                rs.append(data["R"]); ts.append(data["T"])
                            r, t = np.asarray(rs), np.asarray(ts)
                        else:
                            with computational_event("solver_points", len(wl)):
                                r, t = solver.rt_spectrum([v[0] for v in layers] + [substrate], [v[1] * 1e-9 for v in layers] + [0.], wl / 1000., angle, pol, theta_unit="deg")
                        result += weight * mix * {"R": r, "T": t, "A": 1 - r - t}[channel]
            elif n.operator == "resolution":
                sigma = a.get("sigma_nm", 0.)
                if sigma < 0:
                    raise ValueError("spectral resolution must be nonnegative")
                if sigma == 0:
                    result = inputs[0]
                else:
                    # Cell-width quadrature works on nonuniform grids; edge kernel is renormalized.
                    kernel = np.exp(-.5 * ((wl[:, None] - wl[None, :]) / sigma) ** 2) * np.gradient(wl)[None, :]
                    result = kernel @ inputs[0] / kernel.sum(axis=1)
            elif n.operator == "normalize":
                scale = a.get("reference", 1.)
                if scale <= 0:
                    raise ValueError("normalization reference must be positive")
                result = inputs[0] / scale
            elif n.operator == "expression":
                from .operator_sandbox import evaluate_expression
                if np.any(inputs[0]<a.get("input_lower",-np.inf)) or np.any(inputs[0]>a.get("input_upper",np.inf)):
                    raise CompileError("OPERATOR_INPUT_DOMAIN", "new operator input exceeds admitted domain")
                result = evaluate_expression(a["expression"], inputs[0], wl)
            elif n.operator == "drift":
                result = inputs[0] * a.get("gain", 1.) + a.get("offset", 0.) + a.get("slope_per_nm", 0.) * (wl - wl.mean())
            else:
                raise CompileError("UNKNOWN_OPERATOR", n.operator)
            values[n.node_id] = result
        output = np.asarray(values[self.ir.output], dtype=float)
        if not np.all(np.isfinite(output)):
            raise CompileError("NONFINITE_OUTPUT", self.ir.output)
        return output


def compile_model(ir: ModelIR) -> CompiledModel:
    if not isinstance(ir, ModelIR):
        raise TypeError("compile_model requires a typed ModelIR")
    if len(ir.nodes)>64 or len(ir.parameters)>16:raise CompileError("RESOURCE_LIMIT", "model requires at most 64 nodes and 16 parameters")
    seen = {}
    params = {p.name: p for p in ir.parameters}
    for n in ir.nodes:
        if n.node_id in seen:
            raise CompileError("DUPLICATE_NODE", n.node_id)
        if n.operator not in OPERATORS:
            raise CompileError("UNKNOWN_OPERATOR", n.operator)
        if any(i not in seen for i in n.inputs):
            raise CompileError("INVALID_DAG", "inputs must precede consumers")
        if n.complex_convention != "n+ik;exp(-iwt)":
            raise CompileError("COMPLEX_CONVENTION", n.node_id)
        if n.coordinate != "external":
            raise CompileError("ANGLE_COORDINATE", "internal/external angles cannot be silently mixed")
        allowed_attributes = {
            "constant_nk": {"n", "k"}, "cauchy_nk": {"A", "B_um2", "C_um4", "k"},
            "layer": {"thickness", "shared_bias", "coherence"}, "linear_profile": {"thickness", "shared_bias", "coherence", "segments"}, "stack": set(),
            "tmm": {"n_incident", "aperture_deg", "quadrature_points", "s_fraction"},
            "resolution": {"sigma_nm"}, "normalize": {"reference"},
            "drift": {"gain", "offset", "slope_per_nm"}, "expression": {"expression", "operator_id", "input_lower", "input_upper"},
        }[n.operator]
        if set(n.attrs) - allowed_attributes:
            raise CompileError("UNKNOWN_ATTRIBUTE", str(set(n.attrs) - allowed_attributes))
        manifest=OPERATORS[n.operator]
        if not manifest.domain_requirements or not manifest.error_assumptions or manifest.effect_class!="computation":
            raise CompileError("OPERATOR_CONTRACT", "missing numerical domain/error/effect contract")
        if n.operator=="tmm":
            points=n.attrs.get("quadrature_points",5)
            if isinstance(points,bool) or not isinstance(points,int) or not 1<=points<=64:raise CompileError("RESOURCE_LIMIT", "quadrature_points must be bounded integer")
        types = tuple(seen[i] for i in n.inputs)
        expected = OPERATORS[n.operator].input_types
        if n.operator == "stack":
            valid = bool(types) and types[-1] == "constitutive" and all(t == "geometry" for t in types[:-1])
        else:
            valid = types == expected
        if not valid:
            raise CompileError("PHYSICAL_TYPE", f"{n.operator}: {types} vs {expected}")
        if n.unit != OPERATORS[n.operator].unit and not (n.operator in ("layer", "linear_profile") and n.unit == "um"):
            raise CompileError("UNIT_MISMATCH", n.node_id)
        for k, v in n.attributes:
            if isinstance(v, str) and v.startswith("$"):
                if v[1:] not in params:
                    raise CompileError("UNBOUND_PARAMETER", v)
                expected_unit = n.unit if k in ("thickness", "shared_bias") else {"sigma_nm": "nm", "aperture_deg": "deg", "B_um2": "um2", "C_um4": "um4", "slope_per_nm": "1/nm"}.get(k, "1")
                if params[v[1:]].unit != expected_unit:
                    raise CompileError("PARAMETER_UNIT", f"{v} requires {expected_unit}")
        if n.operator == "expression":
            import hashlib
            from .operator_sandbox import validate_expression
            validate_expression(n.attrs.get("expression", ""))
            if hashlib.sha256(n.attrs["expression"].encode()).hexdigest() != n.attrs.get("operator_id"):
                raise CompileError("OPERATOR_IDENTITY", "expression hash mismatch")
        if n.operator in ("layer", "linear_profile") and "thickness" not in n.attrs:
            raise CompileError("MISSING_ATTRIBUTE", "layer thickness")
        seen[n.node_id] = OPERATORS[n.operator].output_type
    if seen.get(ir.output) != "intensity":
        raise CompileError("OUTPUT_TYPE", "observable must be intensity, not amplitude or geometry")
    return CompiledModel(ir)


def compile_candidate(candidate):
    from .contracts import ModelCandidate
    candidate=ModelCandidate.model_validate(candidate.model_dump(mode="json"))
    from .model_ir import model_ir_from_dict
    return compile_model(model_ir_from_dict(candidate.model_ir))
