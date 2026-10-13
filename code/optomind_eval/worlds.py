"""Independent analytic single-film V1 worlds, separate from policy model compiler.

No empirical/full-wave independence is claimed. These worlds use a closed-form
normal-incidence Airy solution, not the policy's VeriTMM S-matrix implementation.
"""
from dataclasses import dataclass, field
import hashlib
import numpy as np
from optomind_optics.harness.science.world_client import TaskManifest


@dataclass(frozen=True)
class WorldManifest:
    task: TaskManifest
    film_n: float
    thickness_nm: float
    substrate_n: float
    seed: int
    independent_noise_std: float = .002
    shared_calibration_std: float = .001
    dispersion_b_um2: float = 0.
    gain: float = 1.
    offset: float = 0.
    resolution_nm: float = 0.
    reference_grade: str = "exact"
    reference_error: float | None = 1e-12
    expressibility_audit: str = "sealed"
    identifiability_audit: str = "sealed"
    provenance: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        if self.film_n <= 0 or self.substrate_n <= 0 or self.thickness_nm < 0:
            raise ValueError("invalid optical world")
        if self.independent_noise_std <= 0 or self.shared_calibration_std < 0:
            raise ValueError("invalid measurement error")


def airy_intensity(world: WorldManifest, wavelengths_nm, channel):
    wl = np.asarray(wavelengths_nm, float)
    if np.any(wl <= 0) or not np.all(np.isfinite(wl)):
        raise ValueError("positive finite wavelengths required")
    n = world.film_n + world.dispersion_b_um2 / (wl / 1000.) ** 2
    r01 = (1 - n) / (1 + n)
    r12 = (n - world.substrate_n) / (n + world.substrate_n)
    phase = np.exp(4j * np.pi * n * world.thickness_nm / wl)
    r = (r01 + r12 * phase) / (1 + r01 * r12 * phase)
    reflectance = np.abs(r) ** 2
    result = {"R": reflectance, "T": 1 - reflectance, "A": np.zeros_like(reflectance)}[channel]
    if world.resolution_nm:
        kernel = np.exp(-.5 * ((wl[:, None] - wl[None, :]) / world.resolution_nm) ** 2) * np.gradient(wl)[None, :]
        result = kernel @ result / kernel.sum(axis=1)
    return world.gain * result + world.offset


FAMILY_CATALOG = {
    "F0": "model-sufficient noiseless-forward reference with instrument noise",
    "F1": "unknown film thickness and index",
    "F2": "shared manufacturing thickness displacement",
    "F3": "dispersive constitutive law required",
    "F4": "constitutive law plus calibration combination",
    "F5": "observation gain/offset missing from initial model",
    "F6": "matched-medium observationally nonidentifiable thickness",
    "F7": "measurement action cost change",
    "F8": "finite spectral resolution required",
    "F9": "unsupported: periodic/full-wave task extension",
    "F10": "unsupported: public measured-data extension",
    "F11": "unsupported: prospective physical experiment extension",
}


def make_world(family, *, task_id, split, seed, group_id=None):
    if family not in FAMILY_CATALOG or family in ("F9", "F10", "F11"):
        raise NotImplementedError(FAMILY_CATALOG.get(family, family))
    if split not in ("S", "T", "V", "P", "A", "O"):
        raise ValueError("unknown split")
    rng = np.random.default_rng(seed)
    n, d = rng.uniform(1.6, 2.3), rng.uniform(60, 180)
    opts = {}
    if family == "F2": d += 12.
    if family in ("F3", "F4"): opts["dispersion_b_um2"] = .035
    if family in ("F4", "F5"): opts.update(gain=.94, offset=.015)
    if family == "F6": n = 1.; opts["identifiability_audit"] = "thickness unobservable for incident-index matched layer"
    if family == "F8": opts.update(resolution_nm=12., reference_grade="high_budget_reference", reference_error=None)
    group = group_id or f"lineage:{seed}"
    task = TaskManifest(task_id, group, split, {"geometry": "single_planar_film", "wavelength_domain_nm": [400., 900.], "angle_deg": 0., "initial_model": "constant_index_ideal_observation", "evidence_tier": "V1_synthetic", "simulator_dependence": "independent Airy implementation; shared Maxwell assumptions"}, ("R", "T"), {"R": 4. if family == "F7" else 1., "T": 2.})
    provenance = (("seed_lineage", str(seed)), ("sample_batch", group), ("world_fingerprint", hashlib.sha256(f"{n},{d},{opts}".encode()).hexdigest()))
    return WorldManifest(task, n, d, 1.5, seed, provenance=provenance, **opts)


def audit_splits(worlds):
    seen = {}
    for world in worlds:
        entries = (("group_id", world.task.group_id),) + world.provenance
        for kind, identity in entries:
            key = (kind, identity)
            if key in seen and seen[key] != world.task.split:
                raise ValueError(f"split provenance conflict: {kind}")
            seen[key] = world.task.split
    return {"passed": True, "world_count": len(worlds), "checked": sorted({k for k, _ in seen}), "limitations": "Declared provenance and exact fingerprints only; physical near-duplicate screening needs expert audit."}
