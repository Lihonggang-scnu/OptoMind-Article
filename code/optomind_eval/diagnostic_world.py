from dataclasses import replace
import numpy as np

from optomind_optics.harness.science.diagnostic_design import DiagnosticDatum, DiagnosticForward, MechanismFamily
from optomind_optics.harness.science.model_ir import IRNode, ModelIR, ParameterSpec


def reversal_family(family_id, reverse=False, *, thickness_scale=1., variable_index=False):
    nodes = []; layers = []; parameters = []
    for i, (n, d) in enumerate(zip((1.46, 2.4, 1.8), (80., 110., 50.))):
        d *= thickness_scale
        name = "sample.d" + str(i)
        index_name = "sample.n" + str(i)
        nodes.extend((IRNode("n" + str(i), "constant_nk", attributes=(("n", "$" + index_name if variable_index else n),)),
            IRNode("l" + str(i), "layer", ("n" + str(i),), (("thickness", "$" + name),), unit="nm")))
        layers.append("l" + str(i))
        parameters.append(ParameterSpec(name, d * .65, d * 1.35, d, "nm"))
        if variable_index:
            parameters.append(ParameterSpec(index_name, n * .95, n * 1.05, n))
    nodes.extend((IRNode("air", "constant_nk", attributes=(("n", 1.),)),
        IRNode("stack", "stack", tuple(layers[::-1] if reverse else layers) + ("air",), unit="nm"), IRNode("R", "tmm", ("stack",))))
    return MechanismFamily(family_id, ModelIR(tuple(nodes), "R", tuple(parameters), (400., 900.)))


class SyntheticDiagnosticProvider:
    evidence_tier = "V1_synthetic"
    noise_validated = True

    def __init__(self, *, family, parameters, config, seed=9127, mismatch_layer=None):
        if mismatch_layer:
            n, thickness = mismatch_layer
            nodes = [IRNode("hidden_n", "constant_nk", attributes=(("n", float(n)),)), IRNode("hidden_layer", "layer", ("hidden_n",), (("thickness", float(thickness)),), unit="nm")]
            for node in family.model.nodes:
                nodes.append(replace(node, inputs=("hidden_layer",) + node.inputs) if node.operator == "stack" else node)
            family = replace(family, model=replace(family.model, nodes=tuple(nodes)))
        self.__family = family
        self.__parameters = dict(parameters)
        self.__forward = DiagnosticForward(replace(config, max_prediction_calls=1000000, max_solver_points=100000000))
        self.__noise = config.noise
        self.__rng = np.random.default_rng(seed)
        self.__receipts = {}
        self.__sessions = {}

    def acquire(self, action, *, request_id):
        if request_id in self.__receipts:
            receipt = self.__receipts[request_id]
            if receipt.action != action:
                raise ValueError("synthetic acquisition idempotency binding changed")
            return receipt
        tolerance = tuple(self.__rng.uniform(-layer.tolerance_nm, layer.tolerance_nm) for layer in action.frontend.layers)
        mean = self.__forward.predict(self.__family, self.__parameters, action, tolerance)
        if action.session_id not in self.__sessions:
            self.__sessions[action.session_id] = (self.__rng.normal(0, self.__noise.offset_std), self.__rng.normal(0, self.__noise.gain_std))
        offset, gain = self.__sessions[action.session_id]
        values = mean * (1 + gain) + offset + self.__rng.normal(0, self.__noise.measurement_std, len(mean))
        covariance = np.eye(len(mean)) * self.__noise.measurement_std ** 2
        receipt = DiagnosticDatum("synthetic:" + request_id, action, tuple(map(float, values)), tuple(tuple(map(float, row)) for row in covariance))
        self.__receipts[request_id] = receipt
        return receipt
