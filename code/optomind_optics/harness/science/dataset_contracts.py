from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class DatasetCard:
    dataset_id: str
    source_url: str
    license_id: str
    files_sha256: tuple[tuple[str, str], ...]
    sample_groups: tuple[tuple[str, str], ...]
    conditions: tuple[tuple[str, str | None], ...]
    calibration: str | None
    uncertainty: str | None
    evidence_level: str
    permitted_uses: tuple[str, ...]
    missingness: tuple[str, ...]

    def __post_init__(self):
        if not self.dataset_id or not self.source_url.startswith('https://') or not self.license_id:
            raise ValueError('dataset requires identity, source and explicit license')
        if self.evidence_level not in {'raw_measured', 'author_processed_measured', 'digitized_measured'}:
            raise ValueError('material constants and simulated spectra are not measured-task datasets')
        if not self.files_sha256 or len(dict(self.files_sha256)) != len(self.files_sha256):
            raise ValueError('nonempty uniquely named source files required')
        for name, digest in self.files_sha256:
            if not name or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('invalid measured source hash')
        if not self.sample_groups or len(dict(self.sample_groups)) != len(self.sample_groups):
            raise ValueError('each sample must have exactly one provenance group')
        if 'quantitative_inference' in self.permitted_uses and any(dict(self.conditions).get(key) is None for key in ('angle_deg','polarization','wavelength_units','sample_geometry')):
            raise ValueError('quantitative inference requires physical measurement conditions')
        if 'quantitative_inference' in self.permitted_uses and (not self.calibration or not self.uncertainty or self.missingness):
            raise ValueError('quantitative inference needs calibrated uncertainty and complete required conditions')

    def verify_files(self, root: str | Path) -> None:
        root=Path(root).resolve()
        for name, expected in self.files_sha256:
            path=(root/name).resolve()
            if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise ValueError('source file missing, outside dataset, or modified')

    @property
    def content_hash(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class DatasetSplitManifest:
    dataset_hash: str
    group_splits: tuple[tuple[str, str], ...]

    def validate(self, card: DatasetCard):
        groups=set(dict(card.sample_groups).values())
        split=dict(self.group_splits)
        if self.dataset_hash!=card.content_hash or len(split)!=len(self.group_splits) or set(split)!=groups:
            raise ValueError('split must bind the dataset and assign whole provenance groups exactly once')
        if not set(split.values()).issubset({'train','validation','promotion','audit'}):
            raise ValueError('unknown dataset split')

    def sample_split(self, card: DatasetCard, sample_id: str):
        self.validate(card)
        return dict(self.group_splits)[dict(card.sample_groups)[sample_id]]
