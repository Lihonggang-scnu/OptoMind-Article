#!/usr/bin/env python3
"""Export an adopted scientific result for existing Article authoring, offline."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-root',required=True,type=Path)
    parser.add_argument('--selected-result-ref',required=True,type=Path,
                        help='JSON file containing the exact immutable ObjectRef')
    parser.add_argument('--out-dir',required=True,type=Path)
    args=parser.parse_args(argv)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from optomind_optics.harness.science.contracts import ObjectRef
    from optomind_optics.harness.science.authoring_source import prepare_scientific_authoring_source
    try:
        ref=ObjectRef.model_validate_json(args.selected_result_ref.read_text())
        selection=prepare_scientific_authoring_source(args.repository_root,ref,args.out_dir)
    except (ValueError,TypeError,KeyError,OSError) as exc:
        parser.exit(2,'Scientific source export failed: '+str(exc)+'\n')
    print(json.dumps({'source_kind':'scientific_result','source_pipeline_dir':str(args.out_dir.resolve()),
        'scientific_selection':str((args.out_dir/'SCIENTIFIC_AUTHORING_SELECTION.json').resolve()),
        'selected_result_ref':selection.selected_result_ref.model_dump(mode='json'),
        'provider_calls':0},sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
