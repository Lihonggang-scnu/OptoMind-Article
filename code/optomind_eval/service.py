"""Capability-limited evaluator service; terminal answers remain sealed server-side."""
from dataclasses import asdict
import hashlib
import json
import math
import numpy as np
from .worlds import airy_intensity, audit_splits
from optomind_optics.harness.science.world_client import MeasurementReceipt


class MeasurementService:
    def __init__(self, worlds, *, budget):
        worlds = tuple(worlds)
        audit_splits(worlds)
        if len({w.task.task_id for w in worlds}) != len(worlds):
            raise ValueError("duplicate task IDs")
        if not math.isfinite(budget) or budget < 0:
            raise ValueError("finite nonnegative budget required")
        if any(not math.isfinite(cost) or cost < 0 for w in worlds for cost in w.task.cost_model.values()):
            raise ValueError("invalid measurement cost model")
        self.__worlds = {w.task.task_id: w for w in worlds}
        self.__remaining = float(budget)
        self.__purchases = {}
        self.__terminal = {}
        self.__calibration = {}

    def handle(self, request):
        try:
            operation = request["operation"]
            world = self.__worlds[request["task_id"]]
            if operation == "purchase":
                return self.__purchase(world, request)
            if operation == "terminal":
                return self.__submit(world, request)
            raise PermissionError("unsupported evaluator operation")
        except (KeyError, ValueError, PermissionError) as exc:
            return {"error": str(exc)}

    def __purchase(self, world, request):
        channel = request["channel"]
        if channel not in world.task.allowed_actions:
            raise PermissionError("channel not available for purchase")
        request_id = request["request_id"]
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("nonempty request ID required")
        key = (world.task.task_id, request_id)
        canonical = json.dumps(request, sort_keys=True, allow_nan=False)
        if key in self.__purchases:
            previous, response = self.__purchases[key]
            if canonical != previous:
                raise ValueError("idempotency key reused with different request")
            return response
        wl = np.asarray(request["wavelengths_nm"], float)
        lower, upper = world.task.visible_spec["wavelength_domain_nm"]
        if wl.ndim != 1 or len(wl) < 2 or len(wl) > 4096 or np.any(np.diff(wl) <= 0) or wl.min() < lower or wl.max() > upper:
            raise ValueError("invalid measurement grid")
        cost = world.task.cost_model[channel]
        if self.__remaining < cost:
            raise PermissionError("measurement budget exhausted")
        # Independent per-acquisition noise and a genuinely shared batch calibration draw.
        secret = hashlib.sha256(f"{world.seed}:{world.task.task_id}:{request_id}".encode()).digest()
        rng = np.random.default_rng(int.from_bytes(secret[:8], "big"))
        group = f"{world.task.group_id}:instrument"
        if group not in self.__calibration:
            shared_rng = np.random.default_rng(int.from_bytes(hashlib.sha256(f"{world.seed}:{group}".encode()).digest()[:8], "big"))
            self.__calibration[group] = shared_rng.normal()
        prediction = airy_intensity(world, wl, channel)
        values = prediction + self.__calibration[group] * world.shared_calibration_std + rng.normal(0, world.independent_noise_std, len(wl))
        rid = hashlib.sha256(canonical.encode()).hexdigest()
        receipt = MeasurementReceipt(rid, world.task.task_id, request_id, channel, tuple(map(float, wl)), tuple(map(float, values)), tuple(tuple(map(float, row)) for row in np.eye(len(wl)) * world.independent_noise_std**2), group, world.shared_calibration_std, cost, world.task.evaluator_version, rid)
        response = asdict(receipt)
        self.__remaining -= cost
        self.__purchases[key] = (canonical, response)
        return response

    def __submit(self, world, request):
        sid = request["submission_id"]
        key = (world.task.task_id, sid)
        design = request["selected_design"]
        if not isinstance(sid, str) or not sid or not isinstance(design, dict):
            raise ValueError("terminal submission requires ID and selected design")
        canonical = json.dumps(design, sort_keys=True, allow_nan=False)
        if key in self.__terminal:
            if self.__terminal[key][0] != canonical:
                raise ValueError("terminal submission ID conflict")
            return self.__terminal[key][2]
        # Parameter recovery is a declared benchmark score, not design utility.
        # Nonidentifiable F6 still admits equivalence: score raw noiseless predictions.
        from dataclasses import replace
        estimate = replace(world, thickness_nm=float(design["thickness_nm"]), film_n=float(design["film_n"]))
        grid = np.linspace(400, 900, 501)
        score = float(np.mean((airy_intensity(estimate, grid, "R") - airy_intensity(world, grid, "R")) ** 2))
        sealed = {"task_id": world.task.task_id, "submission_id": sid, "prediction_mse": score, "reference_grade": world.reference_grade, "reference_error": world.reference_error}
        # Salt prevents a public digest from acting as a score-guessing oracle.
        digest = hashlib.sha256((str(world.seed) + json.dumps(sealed, sort_keys=True)).encode()).hexdigest()
        response = {"submission_id": sid, "status": "sealed", "sealed_digest": digest}
        self.__terminal[key] = (canonical, sealed, response)
        return response


def assert_production_isolation(*, policy_uid, evaluator_uid, policy_can_read_world_store, evaluator_package_in_policy):
    """Fail closed until deployment proves both filesystem and process isolation."""
    if policy_uid == evaluator_uid or policy_can_read_world_store or evaluator_package_in_policy:
        raise PermissionError("independent evaluator requires separate principal, inaccessible world store, and evaluator-free policy image")
    return {"isolation": "deployment_attested", "caveat": "caller must obtain these facts from OS/container policy"}
