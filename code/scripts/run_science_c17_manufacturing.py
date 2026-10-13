#!/usr/bin/env python3
"""Separate frozen finite manufacturing audit; network disabled."""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'code'),str(ROOT/'veritmm')]
def offline_audit(event,args):
    if event.startswith('socket.'):
        raise RuntimeError('Offline manufacturing evaluation forbids network')
sys.addaudithook(offline_audit)
from optomind_eval.manufacturing_tasks import run_manufacturing_evaluation
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--tasks-per-case',type=int,default=24,help='Nondefault runs are labeled engineering fixtures')
    args=parser.parse_args()
    print(json.dumps(run_manufacturing_evaluation(args.output_dir,tasks_per_case=args.tasks_per_case),indent=2))
