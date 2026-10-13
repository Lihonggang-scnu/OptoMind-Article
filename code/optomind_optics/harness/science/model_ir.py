"""Immutable, versioned optical model DAG. Model identity excludes parameter spelling."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import hashlib
import json
import math


class CompileError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    lower: float
    upper: float
    initial: float
    unit: str = "1"
    namespace: str = "sample"

    def __post_init__(self):
        if not self.name or not all(map(math.isfinite, (self.lower, self.upper, self.initial))):
            raise ValueError("parameters require names and finite values")
        if not self.lower < self.upper or not self.lower <= self.initial <= self.upper:
            raise ValueError("invalid parameter bounds")


@dataclass(frozen=True)
class IRNode:
    node_id: str
    operator: str
    inputs: tuple[str, ...] = ()
    attributes: tuple[tuple[str, object], ...] = ()
    unit: str = "1"
    coordinate: str = "external"
    complex_convention: str = "n+ik;exp(-iwt)"

    def __post_init__(self):
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "attributes", tuple((k, tuple(v) if isinstance(v, list) else v) for k, v in self.attributes))
        if len(dict(self.attributes)) != len(self.attributes):
            raise ValueError("duplicate node attribute")
        if any(isinstance(v, (dict, list, set)) for _, v in self.attributes):
            raise ValueError("IR attributes must be immutable scalar or tuple values")

    @property
    def attrs(self):
        return dict(self.attributes)


@dataclass(frozen=True)
class ModelIR:
    nodes: tuple[IRNode, ...]
    output: str
    parameters: tuple[ParameterSpec, ...] = ()
    domain_nm: tuple[float, float] = (200., 2500.)
    version: str = "optical-ir/1"

    def __post_init__(self):
        if self.version != "optical-ir/1":
            raise CompileError("UNSUPPORTED_IR_VERSION", self.version)
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "parameters", tuple(self.parameters))
        if len({p.name for p in self.parameters}) != len(self.parameters):
            raise ValueError("duplicate parameter name")
        if not 0 < self.domain_nm[0] < self.domain_nm[1]:
            raise ValueError("invalid wavelength domain")

    @property
    def structural_hash(self):
        ids = {n.node_id: i for i, n in enumerate(self.nodes)}
        # Alpha-renaming preserves parameter sharing, bounds, units, and namespaces.
        names = {}
        def normalize(value):
            if isinstance(value, str) and value.startswith("$"):
                name = value[1:]
                if name not in names:
                    names[name] = len(names)
                return {"parameter": names[name]}
            if isinstance(value, tuple):
                return [normalize(v) for v in value]
            return value
        nodes = [{"op": n.operator, "inputs": [ids[i] for i in n.inputs], "attrs": [(k, normalize(v)) for k, v in sorted(n.attributes)], "unit": n.unit, "coordinate": n.coordinate, "complex": n.complex_convention} for n in self.nodes]
        for parameter in self.parameters:
            if parameter.name not in names: names[parameter.name] = len(names)
        specs = {p.name: p for p in self.parameters}
        params = [{k: v for k, v in asdict(specs[name]).items() if k not in ("name", "initial")} for name in names]
        payload = (self.version, nodes, ids[self.output], params, self.domain_nm)
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class OperatorManifest:
    name: str
    input_types: tuple[str, ...]
    output_type: str
    unit: str
    version: str = "1"
    sample_support: bool = False
    log_prob_support: bool = False
    resources: str = "numpy; VeriTMM for tmm"
    license: str = "repository license"
    domain_requirements: tuple[str, ...] = ()
    error_assumptions: tuple[str, ...] = ()
    reference_checks: tuple[str, ...] = ()
    cost_model: str = "deterministic array operation; metered enclosing forward"
    effect_class: str = "computation"



OPERATORS = {
    "constant_nk": OperatorManifest("constant_nk", (), "constitutive", "1"),
    "cauchy_nk": OperatorManifest("cauchy_nk", (), "constitutive", "1"),
    "layer": OperatorManifest("layer", ("constitutive",), "geometry", "nm"),
    "linear_profile": OperatorManifest("linear_profile", ("constitutive", "constitutive"), "geometry", "nm"),
    "stack": OperatorManifest("stack", ("geometry*", "constitutive"), "stack", "nm"),
    "tmm": OperatorManifest("tmm", ("stack",), "intensity", "1"),
    "resolution": OperatorManifest("resolution", ("intensity",), "intensity", "1"),
    "normalize": OperatorManifest("normalize", ("intensity",), "intensity", "1"),
    "expression": OperatorManifest("expression", ("intensity",), "intensity", "1"),
    "drift": OperatorManifest("drift", ("intensity",), "intensity", "1"),
}


# Versioned executable contracts, consumed by the compiler and persisted by
# model_contract_report. These are explicit validity/error assumptions, not
# expert or empirical certificates.
_PRIMITIVE_CONTRACTS = {
    "constant_nk": (("n>0 and k>=0 on requested grid",), ("constant over declared wavelength domain; no empirical material error bound",), ("constant constitutive law",)),
    "cauchy_nk": (("positive index, passive k; nm converted to um in dispersion law",), ("A+B/lambda^2+C/lambda^4 model; wavelength validity supplied by model",), ("B=C=0 returns constant law",)),
    "layer": (("nonnegative physical thickness including shared bias", "nm or um unit"), ("homogeneous isotropic layer",), ("zero-thickness limit",)),
    "linear_profile": (("linear index interpolation; 1..4096 slices",), ("midpoint piecewise-constant approximation; no automatic discretization error certificate",), ("equal-endpoint homogeneous limit", "slice refinement")),
    "stack": (("ordered planar finite layers and final semi-infinite substrate",), ("no lateral structure or roughness model",), ("empty stack Fresnel interface",)),
    "tmm": (("external incidence below grazing; s/p intensity only", "coherent or intensity-incoherent planar propagation"), ("finite angular quadrature; shared Maxwell assumptions; not empirical validation",), ("independent Airy single-film", "Byrnes reference", "lossless energy")),
    "resolution": (("sigma_nm>=0; ordered finite wavelength grid",), ("cell-width Gaussian quadrature; edge kernel renormalized",), ("sigma=0 identity", "constant signal preservation", "grid refinement")),
    "normalize": (("positive dimensionless fixed reference",), ("reference uncertainty requires separate joint likelihood; operator alone treats it fixed",), ("reference=1 identity",)),
    "drift": (("finite gain/offset/slope; slope in inverse nm",), ("affine instrument law on centered requested grid; not a physical energy law",), ("unit gain zero offset/slope identity",)),
    "expression": (("validated dimensionless restricted arithmetic DSL; independent admission required for new science operators",), ("domain and numerical references belong to admission receipt; no arbitrary code",), ("source-bound reference/limit/refinement/gradient tests",)),
}
from dataclasses import replace as _replace
OPERATORS = {name:_replace(manifest,domain_requirements=_PRIMITIVE_CONTRACTS[name][0],
    error_assumptions=_PRIMITIVE_CONTRACTS[name][1],reference_checks=_PRIMITIVE_CONTRACTS[name][2],
    cost_model=("number of polarization x aperture solver calls and wavelength points; layer count additionally affects measured wall time" if name=="tmm" else manifest.cost_model))
    for name,manifest in OPERATORS.items()}


def model_contract_report(ir):
    return {"schema_version":"model-operator-contracts/1","model_hash":ir.structural_hash,
        "domain_nm":ir.domain_nm,"operators":{n.operator:asdict(OPERATORS[n.operator]) for n in ir.nodes},
        "node_count":len(ir.nodes),"parameter_count":len(ir.parameters),
        "probability_semantics":"deterministic forwards; stochastic likelihood and prior must be declared separately",
        "scientific_validation":"contracts and numerical tests do not replace expert/empirical signoff"}


def model_ir_from_dict(payload):
    """Load the canonical ModelCandidate.model_ir payload without accepting unknown fields."""
    allowed = {"nodes", "output", "parameters", "domain_nm", "version"}
    if set(payload) - allowed:
        raise CompileError("UNKNOWN_IR_FIELD", str(sorted(set(payload) - allowed)))
    return ModelIR(nodes=tuple(IRNode(**n) for n in payload["nodes"]), output=payload["output"], parameters=tuple(ParameterSpec(**p) for p in payload.get("parameters", ())), domain_nm=tuple(payload.get("domain_nm", (200., 2500.))), version=payload.get("version", "optical-ir/1"))
