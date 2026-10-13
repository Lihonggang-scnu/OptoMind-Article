"""Joint raw-data likelihood with shared calibration covariance and derived-view exclusion."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import numpy as np
from scipy.linalg import solve_triangular


@dataclass(frozen=True)
class LikelihoodBlock:
    observation_id: str
    wavelengths_nm: tuple[float, ...]
    values: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]
    channel: str = "R"
    angle_deg: float = 0.
    calibration_group: str = ""
    calibration_std: float = 0.
    derived_from: tuple[str, ...] = ()
    gaussian_validated: bool = False

    def __post_init__(self):
        n = len(self.values)
        c = np.asarray(self.covariance)
        if not self.observation_id or n == 0 or len(self.wavelengths_nm) != n or c.shape != (n, n):
            raise ValueError("invalid raw likelihood block dimensions")
        if not np.all(np.isfinite(c)) or not np.allclose(c, c.T) or not np.all(np.isfinite(self.values)):
            raise ValueError("nonfinite observations or nonsymmetric covariance")
        if not np.isfinite(self.calibration_std) or self.calibration_std < 0 or (self.calibration_std and not self.calibration_group):
            raise ValueError("shared calibration needs a group and nonnegative std")
        np.linalg.cholesky(c)


class JointLikelihood:
    def __init__(self, blocks):
        self.blocks = tuple(b for b in blocks if not b.derived_from)
        ids = [b.observation_id for b in self.blocks]
        if not self.blocks or len(ids) != len(set(ids)):
            raise ValueError("requires unique raw observations; repeated/derived views are not new samples")
        sizes = [len(b.values) for b in self.blocks]
        self.slices = tuple(slice(sum(sizes[:i]), sum(sizes[:i+1])) for i in range(len(sizes)))
        self.values = np.concatenate([b.values for b in self.blocks])
        covariance = np.zeros((sum(sizes), sum(sizes)))
        groups = {}
        for b, sl in zip(self.blocks, self.slices):
            covariance[sl, sl] = b.covariance
            if b.calibration_group:
                loading = groups.setdefault(b.calibration_group, np.zeros(sum(sizes)))
                loading[sl] = b.calibration_std
        for loading in groups.values():
            covariance += np.outer(loading, loading)
        self.covariance = covariance
        self.cholesky = np.linalg.cholesky(covariance)
        self.gaussian_validated = all(b.gaussian_validated for b in self.blocks)

    @property
    def evidence_hashes(self):
        """Bind every consumed value, coordinate, covariance and noise assumption."""
        from .contracts import canonical_hash
        return tuple(canonical_hash(asdict(block)) for block in self.blocks)

    def predict(self, model, parameters):
        return np.concatenate([model.predict(parameters, b.wavelengths_nm, angle_deg=b.angle_deg, channel=b.channel) for b in self.blocks])

    def whiten(self, residual):
        return solve_triangular(self.cholesky, residual, lower=True)

    def residual(self, model, parameters):
        return self.whiten(self.predict(model, parameters) - self.values)

    def log_prob(self, model, parameters):
        if not self.gaussian_validated:
            raise ValueError("Gaussian assumptions not validated; use scenario loss")
        r = self.residual(model, parameters)
        return float(-.5 * (r @ r + 2 * np.log(np.diag(self.cholesky)).sum() + len(r) * np.log(2 * np.pi)))


def blocks_from_observations(observations):
    """Only fully specified raw channels can enter inference; missing errors are not guessed."""
    result = []
    acquisitions = set()
    for observation in observations:
        from .contracts import Observation
        observation = Observation.model_validate(observation.model_dump(mode="json"))
        if observation.acquisition_id in acquisitions:
            raise ValueError("duplicate acquisition; multiple projections cannot add samples")
        acquisitions.add(observation.acquisition_id)
        if len(observation.raw_blocks) > 1 and observation.quality.get("cross_block_noise_model") not in ("independent", "shared_calibration_only"):
            raise ValueError("multiple raw blocks require an explicit cross-block noise model")
        for raw in observation.raw_blocks:
            required = {"block_id", "wavelengths_nm", "values", "covariance"}
            if not required <= raw.keys():
                raise ValueError("raw block lacks explicit wavelength/value/covariance contract")
            if raw.get("derived_from"):
                raise ValueError("derived view supplied as raw likelihood block")
            result.append(LikelihoodBlock(observation_id=f"{observation.acquisition_id}:{raw['block_id']}", wavelengths_nm=tuple(raw["wavelengths_nm"]), values=tuple(raw["values"]), covariance=tuple(tuple(r) for r in raw["covariance"]), channel=raw.get("channel", "R"), angle_deg=raw.get("angle_deg", 0.), calibration_group=raw.get("calibration_group", ""), calibration_std=raw.get("calibration_std", 0.), gaussian_validated=raw.get("gaussian_validated", False)))
    return tuple(result)
