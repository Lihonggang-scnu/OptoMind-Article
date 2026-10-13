#!/usr/bin/env python3
"""Offline genuine production-loop M x E evaluation, frozen plan required."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'code'),str(ROOT/'veritmm'),str(ROOT.parent/'OptoMInd-Review')]
def offline_audit(event,args):
    if event.startswith('socket.'):raise RuntimeError('C17 offline factorial forbids network')
sys.addaudithook(offline_audit)
from optomind_eval.factorial_runner import run_factorial_evaluation
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output-dir',required=True);p.add_argument('--plan',required=True)
    p.add_argument('--phase',choices=('pilot','audit'),required=True)
    args=p.parse_args();print(json.dumps(run_factorial_evaluation(args.output_dir,args.plan,phase=args.phase),indent=2))
