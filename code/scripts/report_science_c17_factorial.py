#!/usr/bin/env python3
"""Reproduce a factorial report from sealed results only (no policy execution)."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'code')]
from optomind_eval.factorial_statistics import report_factorial
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--results',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();report=report_factorial(args.results)
    with Path(args.output).open('x') as stream:json.dump(report,stream,sort_keys=True,allow_nan=False,indent=2)
    print(json.dumps({'result_seal':report['result_seal'],'output':args.output}))
