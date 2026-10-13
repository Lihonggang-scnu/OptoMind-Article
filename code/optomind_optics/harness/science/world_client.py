"""Policy-visible purchase protocol. This module never imports evaluator worlds."""
from dataclasses import dataclass, asdict
from typing import Callable


@dataclass(frozen=True)
class TaskManifest:
    task_id: str
    group_id: str
    split: str
    visible_spec: dict
    allowed_actions: tuple[str, ...]
    cost_model: dict[str, float]
    evaluator_version: str = "planar-independent/1"


@dataclass(frozen=True)
class MeasurementReceipt:
    receipt_id: str
    task_id: str
    request_id: str
    channel: str
    wavelengths_nm: tuple[float, ...]
    values: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]
    calibration_group: str
    calibration_std: float
    cost: float
    evaluator_version: str
    acquisition_id: str


class WorldClient:
    """Transport-only capability. Server must run under a separate OS principal in production.

    A local callable transport is an integration-test facility, not a security boundary.
    No terminal scores, hidden parameters, seeds, or audit answers are part of this API.
    """
    def __init__(self, transport: Callable[[dict], dict]):
        self.__transport = transport

    def purchase(self, task_id, request_id, channel, wavelengths_nm):
        response = self.__transport({"operation": "purchase", "task_id": task_id, "request_id": request_id, "channel": channel, "wavelengths_nm": list(wavelengths_nm)})
        if "error" in response:
            raise PermissionError(response["error"])
        return MeasurementReceipt(**response)

    def submit_terminal(self, task_id, submission_id, selected_design):
        response = self.__transport({"operation": "terminal", "task_id": task_id, "submission_id": submission_id, "selected_design": selected_design})
        if "error" in response:
            raise PermissionError(response["error"])
        if set(response) != {"submission_id", "status", "sealed_digest"}:
            raise ValueError("terminal endpoint leaked unapproved fields")
        return response


def receipt_to_observation(receipt, *, object_id, source_run_id, candidate_ref=None, objective_ref=None,
                           sample_ref=None, acquisition_identity=None):
    """Bind a paid raw receipt into the shared observation contract, preserving correlation."""
    from .contracts import AcquisitionIdentity, Observation
    import numpy as np
    from .likelihood_blocks import LikelihoodBlock
    if receipt.evaluator_version not in {"planar-independent/1", "multilayer-byrnes/1"} or receipt.channel not in {"R", "T", "A"}:
        raise ValueError("unsupported synthetic receipt version or intensity channel")
    # This specific evaluator declares dimensionless intensity and a Gaussian
    # acquisition model. Do not infer these semantics for arbitrary providers.
    LikelihoodBlock(receipt.acquisition_id, tuple(receipt.wavelengths_nm), tuple(receipt.values),
                    tuple(tuple(row) for row in receipt.covariance), channel=receipt.channel,
                    calibration_group=receipt.calibration_group, calibration_std=receipt.calibration_std,
                    gaussian_validated=True)
    coordinate = np.asarray(receipt.wavelengths_nm, dtype=float)
    if not np.isfinite(coordinate).all() or np.any(coordinate <= 0) or np.any(np.diff(coordinate) <= 0):
        raise ValueError("invalid synthetic receipt wavelength coordinate")
    binding = {"measurement_receipt_id": receipt.receipt_id, "task_id": receipt.task_id, "evaluator_version": receipt.evaluator_version}
    block = {"block_id": receipt.channel, "channel": receipt.channel, "wavelengths_nm": receipt.wavelengths_nm,
             "coordinate": receipt.wavelengths_nm, "coordinate_units": "nm", "units": "1", "values": receipt.values,
             "covariance": receipt.covariance, "calibration_group": receipt.calibration_group,
             "calibration_std": receipt.calibration_std, "gaussian_validated": True}
    if acquisition_identity is not None:
        identity = AcquisitionIdentity.model_validate(acquisition_identity)
        if identity.session_id != receipt.task_id:
            raise ValueError("receipt task differs from acquisition session identity")
        if identity.conditions["coordinate_units"] != "nm":
            raise ValueError("synthetic receipt coordinate units must be nm")
        binding.update(program_ref=identity.program_ref.model_dump(mode="json"),
                       acquisition_identity=identity.model_dump(mode="json"), conditions=dict(identity.conditions))
        block.update({key: identity.conditions[key] for key in ("angle_deg", "polarization")})
    return Observation(object_id=object_id, source_run_id=source_run_id, acquisition_id=receipt.acquisition_id,
        candidate_ref=candidate_ref, objective_ref=objective_ref, sample_ref=sample_ref, binding=binding,
        raw_blocks=(block,), quality={"evidence_tier": "V1_synthetic", "independent_measurement_purchase": True,
            "execution_status": "completed", "numerical_valid": True, "noise_validated": True,
            "validation_scope": receipt.evaluator_version + " synthetic Gaussian intensity acquisition"},
        cost_refs=(receipt.request_id,))


class SyntheticMeasurementProvider:
    """C03 provider adapter for a purchased synthetic channel, never a device claim."""
    def __init__(self, client, *, sample_tasks):
        self.client=client
        self.sample_tasks=dict(sample_tasks)
        self._receipts={}

    def submit(self,key,payload):
        task_id=self.sample_tasks[payload["sample_id"]]
        receipt=self.client.purchase(task_id,key,payload["channel"],payload["arguments"]["wavelengths_nm"])
        result={"state":"completed","cost":{"calls":1.,"cost":receipt.cost},"values":list(receipt.values),"measurement_receipt":asdict(receipt),"evidence_tier":"V1_synthetic"}
        self._receipts[key]=result
        return result

    def status(self,key):
        # This in-memory adapter cannot determine process-loss completion.
        # A missing key is explicitly unknown and the gate will not resubmit.
        return self._receipts.get(key,{"state":"unknown_outcome"})
