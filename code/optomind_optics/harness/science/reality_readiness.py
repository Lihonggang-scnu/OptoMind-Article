from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import StrictModel, canonical_hash
from .dataset_contracts import DatasetCard


class ReadinessItem(StrictModel):
    resource_id: str = Field(min_length=1)
    category: Literal['data', 'sample', 'instrument', 'calibration', 'personnel', 'permission', 'budget']
    status: Literal['verified', 'unknown', 'blocked']
    evidence_refs: tuple[str, ...] = ()
    limitation: str = ''

    @model_validator(mode='after')
    def check_evidence(self):
        if self.status == 'verified' and not self.evidence_refs:
            raise ValueError('verified readiness requires explicit evidence')
        if self.status != 'verified' and not self.limitation:
            raise ValueError('unknown or blocked resource requires explanation')
        return self


class RealityReadinessChecklist(StrictModel):
    schema_version: Literal['reality-readiness.v1'] = 'reality-readiness.v1'
    dataset_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    items: tuple[ReadinessItem, ...]
    assessment_scope: Literal['source_and_software_only'] = 'source_and_software_only'
    hardware_authorized: Literal[False] = False

    @model_validator(mode='after')
    def unique(self):
        if len({item.resource_id for item in self.items}) != len(self.items):
            raise ValueError('duplicate readiness resource identity')
        return self

    @property
    def content_hash(self):
        return canonical_hash(self)

    def pending(self):
        return tuple(item.resource_id for item in self.items if item.status != 'verified')


def assess_dataset_readiness(card: DatasetCard, source_root: str | Path):
    card.verify_files(source_root)
    evidence = (card.source_url, 'dataset:' + card.content_hash)
    items = [ReadinessItem(resource_id='source_files_and_declared_license', category='data', status='verified', evidence_refs=evidence)]
    conditions = dict(card.conditions)
    for field in ('angle_deg', 'polarization', 'wavelength_units', 'sample_geometry'):
        value = conditions.get(field)
        present = value is not None and str(value).strip() != ''
        items.append(ReadinessItem(resource_id=field, category='data', status='verified' if present else 'unknown',
                    evidence_refs=evidence if present else (), limitation='' if present else 'Not established by this dataset card.'))
    for field in ('calibration', 'uncertainty'):
        present = bool(getattr(card, field))
        items.append(ReadinessItem(resource_id=field, category='calibration', status='verified' if present else 'unknown',
                    evidence_refs=evidence if present else (), limitation='' if present else 'A source-qualified measurement description is missing.'))
    quantitative = 'quantitative_inference' in card.permitted_uses
    items.append(ReadinessItem(resource_id='quantitative_inference', category='data', status='verified' if quantitative else 'blocked',
                evidence_refs=evidence if quantitative else (), limitation='' if quantitative else 'Dataset does not grant quantitative inference; no covariance or conditions may be invented.'))
    for resource, category in (('prospective_samples','sample'), ('approved_instrument','instrument'),
                               ('responsible_operator','personnel'), ('laboratory_authorization','permission'),
                               ('approved_expenditure','budget')):
        items.append(ReadinessItem(resource_id=resource, category=category, status='unknown',
                    limitation='Public historical data do not establish a prospective resource or authorization.'))
    return RealityReadinessChecklist(dataset_hash=card.content_hash, items=tuple(items))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset-card', required=True)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    value = json.loads(Path(args.dataset_card).read_text())
    for key in ('files_sha256','sample_groups','conditions'):
        value[key] = tuple(tuple(row) for row in value[key])
    for key in ('permitted_uses','missingness'):
        value[key] = tuple(value[key])
    card = DatasetCard(**value)
    result = assess_dataset_readiness(card, args.source_root)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result.model_dump(mode='json'), stream, ensure_ascii=False, indent=2)
    print(json.dumps({'content_hash':result.content_hash,'pending':result.pending(),'hardware_authorized':False}))


if __name__ == '__main__':
    main()
