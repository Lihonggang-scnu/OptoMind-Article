from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
from pathlib import Path

import numpy as np

from ..science.contracts import Observation
from ..science.dataset_contracts import DatasetCard, DatasetSplitManifest


@dataclass(frozen=True)
class RecordedMeasurement:
    action_id: str
    sample_id: str
    source_file: str
    channel: str
    units: str
    coordinate: tuple[float, ...]
    values: tuple[float, ...]
    conditions: tuple[tuple[str, str | None], ...]
    covariance: tuple[tuple[float, ...], ...] | None = None
    coordinate_column: int = 0
    value_column: int = 1
    delimiter: str | None = ","
    skip_rows: int = 0
    max_rows: int | None = None
    coordinate_units: str = "nm"
    coordinate_scale: float = 1.0
    value_scale: float = 1.0

    def __post_init__(self):
        if not self.action_id or not self.channel or not self.units or not self.coordinate or len(self.coordinate)!=len(self.values):
            raise ValueError('recorded measurement requires complete aligned channel data')
        if not np.isfinite(self.coordinate).all() or not np.isfinite(self.values).all():
            raise ValueError('measurement contains nonfinite data')
        if self.covariance is not None:
            covariance=np.asarray(self.covariance)
            if covariance.shape!=(len(self.values),len(self.values)) or not np.allclose(covariance,covariance.T) or np.linalg.eigvalsh(covariance).min()<=0:
                raise ValueError('declared covariance must be positive definite')


class MeasuredObservationAdapter:
    def __init__(self, card: DatasetCard, split: DatasetSplitManifest, records: tuple[RecordedMeasurement, ...],
                 source_root: str | Path, *, accessible_split: str):
        card.verify_files(source_root)
        split.validate(card)
        if accessible_split not in {'train','validation','promotion','audit'}:
            raise ValueError('explicit accessible split required')
        self.card,self.split,self.accessible_split=card,split,accessible_split
        self.source_root=Path(source_root)
        self.records={record.action_id:record for record in records}
        if len(self.records)!=len(records):
            raise ValueError('recorded menu action IDs must be unique')
        sources=dict(card.files_sha256)
        source_splits={}
        for record in records:
            if 'quantitative_inference' in card.permitted_uses:
                conditions = dict(record.conditions)
                if any(conditions.get(key) != value for key, value in card.conditions if value is not None):
                    raise ValueError('quantitative record conditions differ from source-qualified dataset')
                if conditions.get('wavelength_units') != record.coordinate_units:
                    raise ValueError('quantitative coordinate units differ from measurement conditions')
            if record.source_file not in sources or record.sample_id not in dict(card.sample_groups):
                raise ValueError('menu record source/sample not bound to dataset')
            if min(record.coordinate_column,record.value_column,record.skip_rows)<0 or (record.max_rows is not None and record.max_rows < 1) or not np.isfinite([record.coordinate_scale,record.value_scale]).all():
                raise ValueError('invalid explicit source transformation')
            source_bytes = (Path(source_root)/record.source_file).read_bytes()
            if hashlib.sha256(source_bytes).hexdigest() != sources[record.source_file]:
                raise ValueError('measurement source changed before parsing')
            data=np.loadtxt(io.BytesIO(source_bytes),delimiter=record.delimiter,skiprows=record.skip_rows,max_rows=record.max_rows,ndmin=2,usecols=(record.coordinate_column,record.value_column))
            if not np.array_equal(data[:,0]*record.coordinate_scale,record.coordinate) or not np.array_equal(data[:,1]*record.value_scale,record.values):
                raise ValueError('menu values differ from source-bound explicit transformation')
            source_key=(sources[record.source_file],record.coordinate_column,record.value_column)
            actual_split=split.sample_split(card,record.sample_id)
            if source_key in source_splits and source_splits[source_key]!=actual_split:
                raise ValueError('same measured acquisition cannot cross dataset splits')
            source_splits[source_key]=actual_split

    def available_actions(self):
        return tuple(key for key, record in self.records.items() if self.split.sample_split(self.card, record.sample_id)==self.accessible_split)

    def read(self, action_id: str, *, run_id: str, required_conditions: dict[str,str] | None=None):
        self.card.verify_files(self.source_root)
        if action_id not in self.available_actions():
            raise ValueError('measurement not recorded or unavailable in this split; interpolation is not acquisition')
        record=self.records[action_id]
        conditions=dict(record.conditions)
        if any(conditions.get(k)!=v for k,v in (required_conditions or {}).items()):
            raise ValueError('unknown or mismatched measurement condition')
        raw_hash=dict(self.card.files_sha256)[record.source_file]
        identity=hashlib.sha256(f'{self.card.content_hash}:{action_id}'.encode()).hexdigest()
        block={'block_id':identity,'channel':record.channel,'units':record.units,'coordinate':list(record.coordinate),'values':list(record.values),
               'source_hash':raw_hash,'source_file':record.source_file,'sample_id':record.sample_id,'coordinate_units':record.coordinate_units}
        if record.coordinate_units == 'nm':
            block['wavelengths_nm']=list(record.coordinate)
        if conditions.get('angle_deg') is not None:
            block['angle_deg']=float(conditions['angle_deg'])
        if record.covariance is not None:
            block['covariance']=[list(row) for row in record.covariance]
        return Observation(object_id=f'measured:{identity}',source_run_id=run_id,acquisition_id=identity,
                           raw_blocks=(block,),artifact_refs=(self.card.source_url,),
                           binding={'dataset_hash':self.card.content_hash,'split':self.accessible_split,'conditions':conditions},
                           quality={'execution_status':'completed','numerical_valid':True,
                                    'noise_validated':record.covariance is not None and 'quantitative_inference' in self.card.permitted_uses,
                                    'evidence_level':self.card.evidence_level,'new_physical_measurement':False,
                                    'physical_range_violations':sum(v<0 or v>1 for v in record.values) if record.channel in {'R','T','A'} and record.units in {'1','dimensionless'} else None,
                                    'acquisition_cost':'historical_not_recorded','menu_access_cost':0})
