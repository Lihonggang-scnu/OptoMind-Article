#!/usr/bin/env python3
"""Run all frozen finite manufacturing pool/cost sensitivities offline."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'code'),str(ROOT/'veritmm'),str(ROOT.parent/'OptoMInd-Review')]
def offline_audit(event,args):
    if event.startswith('socket.'):raise RuntimeError('C17 offline sensitivity forbids network')
sys.addaudithook(offline_audit)
from optomind_eval.manufacturing_sensitivity import run_manufacturing_sensitivity
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output-dir',required=True);p.add_argument('--source-dir',required=True);p.add_argument('--plan',required=True)
    args=p.parse_args();report=run_manufacturing_sensitivity(args.output_dir,args.source_dir,args.plan)
    print(json.dumps(report,indent=2))
