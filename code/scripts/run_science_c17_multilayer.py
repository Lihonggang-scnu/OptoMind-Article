#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'code'),str(ROOT/'veritmm')]
def offline_audit(event,args):
    if event.startswith('socket.'):
        raise RuntimeError('Offline coating evaluation forbids network')
sys.addaudithook(offline_audit)
from optomind_eval.multilayer_tasks import run_multilayer_evaluation
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output-dir',required=True);p.add_argument('--tasks-per-case',type=int,default=12)
    args=p.parse_args()
    print(json.dumps(run_multilayer_evaluation(args.output_dir,tasks_per_case=args.tasks_per_case),indent=2))
