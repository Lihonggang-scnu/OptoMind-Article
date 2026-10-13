from __future__ import annotations

import argparse
import json
from pathlib import Path

from optomind_optics.harness.science.dataset_contracts import DatasetCard, DatasetSplitManifest
from optomind_optics.harness.tools.measured_data_adapter import RecordedMeasurement, MeasuredObservationAdapter
from optomind_research.runtime.artifact_store import atomic_write_json


def main():
    parser=argparse.ArgumentParser(description='Import an explicitly recorded measured menu; no acquisition or interpolation.')
    for name in ('card','split','record','source-root','output','run-id','accessible-split'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    card=DatasetCard(**json.loads(Path(args.card).read_text()))
    split=DatasetSplitManifest(**json.loads(Path(args.split).read_text()))
    record=RecordedMeasurement(**json.loads(Path(args.record).read_text()))
    adapter=MeasuredObservationAdapter(card,split,(record,),args.source_root,accessible_split=args.accessible_split)
    observation=adapter.read(record.action_id,run_id=args.run_id)
    target=Path(args.output)
    if target.exists():
        current=json.loads(target.read_text())
        if current.get('content_hash')!=observation.content_hash:
            raise ValueError('existing import differs; choose a new output path')
    atomic_write_json(target,observation.model_dump(mode='json'))
    print(json.dumps({'output':str(target),'hash':observation.content_hash,'new_physical_measurement':False}))


if __name__=='__main__':
    main()
