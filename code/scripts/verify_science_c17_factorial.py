#!/usr/bin/env python3
"""Read-only physical/statistical verification of sealed factorial artifacts."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
def offline_audit(event,args):
    if event.startswith('socket.'):raise RuntimeError('Offline artifact verification forbids network')
sys.addaudithook(offline_audit)
from optomind_eval.factorial_audit import verify_factorial_artifacts
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--directory',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();report=verify_factorial_artifacts(a.directory)
    with Path(a.output).open('x') as stream:json.dump(report,stream,indent=2,sort_keys=True)
    print(json.dumps(report,indent=2))
